import SwiftUI
import MapKit
import MekenCore

struct ApartmentDetailView: View {
    let initialApartment: Apartment
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var refreshed: Apartment?
    @State private var checking = false
    @State private var verificationMessage: String?
    private var apartment: Apartment { refreshed ?? initialApartment }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    ApartmentArtwork(apartment: apartment).frame(height: 280).clipShape(.rect(cornerRadius: 12))
                    VStack(alignment: .leading, spacing: 10) {
                        Text(apartment.complexName).font(.system(.title, weight: .bold))
                        Text(apartment.priceLabel).font(.title2.weight(.semibold))
                        Text("\(apartment.rooms)-комнатная · \(apartment.areaLabel) · \(apartment.floor) из \(apartment.totalFloors) этажей").font(.subheadline)
                        Label(apartment.address, systemImage: "mappin.and.ellipse").font(.footnote).foregroundStyle(.secondary)
                    }
                    if apartment.isDemo {
                        NoticeCard(text: "Пример квартиры для знакомства с приложением. Адрес, цена и объекты рядом вымышлены; покупка недоступна.")
                    } else {
                        NoticeCard(text: "\(apartment.statusLabel). Сведения получены \(apartment.observedAtLabel). Цена и наличие могут измениться.")
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Почему стоит посмотреть").font(.headline)
                        if apartment.reasons.isEmpty {
                            Text("Укажите бюджет, комнаты и пожелания в подборе — я смогу сопоставить их с этой квартирой.").font(.subheadline).foregroundStyle(.secondary)
                        }
                        ForEach(apartment.reasons, id: \.self) { reason in Label(reason, systemImage: "checkmark.circle.fill").foregroundStyle(Theme.accent).font(.subheadline) }
                        ForEach(apartment.tradeoffs, id: \.self) { reason in Label(reason, systemImage: "info.circle").foregroundStyle(.secondary).font(.subheadline) }
                        Button("Обсудить эту квартиру", systemImage: "bubble.left.and.bubble.right") { dismiss(); store.explain(apartment) }
                            .font(.subheadline.weight(.semibold)).padding(.top, 4).disabled(store.search.isStreaming || store.isPreparingSearch || store.pendingRequest != nil || store.pendingConversation != nil)
                    }
                    Divider()
                    VStack(spacing: 14) {
                        detailRow("Отделка", value: apartment.finish)
                        detailRow("Срок", value: apartment.completion)
                        detailRow("Застройщик", value: apartment.providerName)
                    }
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Инфраструктура проекта").font(.headline)
                        if let bigville = apartment.bigvilleName {
                            Label("Бигвилль «\(bigville)»", systemImage: "building.2").font(.subheadline)
                        }
                        if (apartment.projectFacts ?? []).isEmpty {
                            Text("В подключённых данных застройщика пока нет подробного описания инфраструктуры. Это не означает, что школы или садика нет.").font(.subheadline).foregroundStyle(.secondary)
                        }
                        ForEach(apartment.projectFacts ?? []) { fact in
                            VStack(alignment: .leading, spacing: 7) {
                                Text(fact.name).font(.subheadline.weight(.semibold))
                                Text("\(fact.statusLabel) · \(fact.scopeLabel)").font(.caption).foregroundStyle(fact.state == "operating" ? Theme.accent : Theme.coral)
                                if let opening = fact.expectedOpening { Text("Заявленный срок: \(opening)").font(.caption) }
                                Text(fact.evidence).font(.footnote).foregroundStyle(.secondary)
                                if let url = Apartment.httpsURL(fact.sourceUrl) { Link("Источник застройщика", destination: url).font(.caption) }
                            }.padding(14).frame(maxWidth: .infinity, alignment: .leading).background(Theme.secondary, in: .rect(cornerRadius: 10))
                        }
                    }
                    if let lat = apartment.latitude, let lon = apartment.longitude {
                        Map(initialPosition: .region(MKCoordinateRegion(center: CLLocationCoordinate2D(latitude: lat, longitude: lon), span: MKCoordinateSpan(latitudeDelta: 0.015, longitudeDelta: 0.015)))) {
                            Marker(apartment.complexName, coordinate: CLLocationCoordinate2D(latitude: lat, longitude: lon)).tint(Theme.accent)
                        }.frame(height: 220).clipShape(.rect(cornerRadius: 20)).accessibilityLabel("Расположение жилого комплекса на карте")
                    }
                    VStack(alignment: .leading, spacing: 14) {
                        Text("Дополнительно: объекты рядом").font(.headline)
                        if apartment.amenities.isEmpty {
                            Text("Пока нет подтверждённых данных о школах, садиках и парках рядом. Отсутствие данных не означает, что их нет.").font(.subheadline).foregroundStyle(.secondary)
                        }
                        ForEach(apartment.amenities) { amenity in
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: amenity.symbol).frame(width: 28).foregroundStyle(Theme.accent)
                                VStack(alignment: .leading, spacing: 4) {
                                    Text(amenity.name).font(.subheadline)
                                    if !apartment.isDemo, let url = Apartment.httpsURL(amenity.sourceUrl) { Link("Источник", destination: url).font(.caption) }
                                }
                                Spacer()
                                Text("\(amenity.distanceM) м").font(.subheadline.weight(.medium))
                            }
                        }
                        if apartment.amenities.contains(where: { $0.sourceUrl.contains("openstreetmap.org") }) {
                            Link("© OpenStreetMap contributors · ODbL", destination: URL(string: "https://www.openstreetmap.org/copyright")!).font(.caption2)
                        }
                        Text("Расстояния по прямой. Пеший маршрут и возможность зачисления в школу проверяются отдельно.").font(.caption).foregroundStyle(.secondary)
                    }
                    if let verificationMessage { NoticeCard(text: verificationMessage) }
                    Button {
                        checking = true
                        Task {
                            do {
                                let result = try await store.verify(apartment)
                                refreshed = result.listing
                                verificationMessage = result.verification == "confirmed" ? "Получен ответ застройщика. Текущий статус: \(apartment.statusLabel.lowercased()). Это не бронирование." : "Не удалось получить новое подтверждение наличия. Можно повторить проверку позже или обратиться к застройщику."
                            } catch { verificationMessage = store.friendly(error) }
                            checking = false
                        }
                    } label: { HStack { if checking { ProgressView().tint(.white) }; Text(checking ? "Проверяю наличие" : "Проверить наличие") } }
                    .buttonStyle(PrimaryButtonStyle()).disabled(checking || apartment.isDemo)
                    if apartment.canOpenSource, let url = apartment.safeSourceURL {
                        Link(destination: url) { Label("Открыть у застройщика", systemImage: "arrow.up.right.square").frame(maxWidth: .infinity).font(.subheadline.weight(.semibold)) }
                    }
                }.padding(22)
            }
            .background(Theme.paper).foregroundStyle(Theme.ink)
            .navigationTitle("Квартира").navigationBarTitleDisplayMode(.inline)
             .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button { Task { await store.toggleFavorite(apartment) } } label: {
                        Image(systemName: store.favorites.contains(where: { $0.id == apartment.id }) ? "heart.fill" : "heart")
                    }
                    .accessibilityLabel(store.favorites.contains(where: { $0.id == apartment.id }) ? "Убрать из избранного" : "Сохранить квартиру")
                    .accessibilityIdentifier("detail-favorite")
                    .disabled(store.isMutatingFavorite.contains(apartment.id))
                }
                ToolbarItem(placement: .confirmationAction) { Button("Готово") { dismiss() } }
            }
        }.tint(Theme.accent)
    }
    private func detailRow(_ title: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(title).foregroundStyle(.secondary)
            Spacer(minLength: 20)
            Text(value).multilineTextAlignment(.trailing)
        }.font(.subheadline)
    }
}
