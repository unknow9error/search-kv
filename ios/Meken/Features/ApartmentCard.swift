import SwiftUI
import MekenCore

struct ApartmentCard: View {
    let apartment: Apartment
    var onOpen: () -> Void
    @Environment(AppStore.self) private var store
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ZStack(alignment: .topTrailing) {
                Button(action: onOpen) { ApartmentArtwork(apartment: apartment).frame(height: 195).clipped() }.buttonStyle(.plain)
                Button {
                    Task { await store.toggleFavorite(apartment) }
                } label: {
                    Image(systemName: store.favorites.contains(where: { $0.id == apartment.id }) ? "heart.fill" : "heart")
                        .font(.system(size: 18, weight: .medium)).foregroundStyle(Theme.accent)
                        .frame(width: 44, height: 44).background(.regularMaterial, in: .circle)
                }
                .disabled(store.isMutatingFavorite.contains(apartment.id))
                .padding(12)
                .accessibilityLabel(store.favorites.contains(where: { $0.id == apartment.id }) ? "Убрать из избранного" : "Сохранить квартиру")
            }
            Button(action: onOpen) {
            VStack(alignment: .leading, spacing: 9) {
                Text(apartment.priceLabel).font(.title2.weight(.semibold)).foregroundStyle(Theme.ink)
                HStack(spacing: 7) {
                    Text("\(apartment.rooms)-комн.")
                    Text("·").accessibilityHidden(true)
                    Text(apartment.areaLabel)
                    Text("·").accessibilityHidden(true)
                    Text("\(apartment.floor)/\(apartment.totalFloors) эт.")
                }.font(.subheadline).foregroundStyle(Theme.ink)
                Text(apartment.complexName).font(.headline)
                Text("\(apartment.city) · \(apartment.district.isEmpty ? apartment.address : apartment.district)")
                    .font(.footnote).foregroundStyle(.secondary).lineLimit(2)
                if let reason = apartment.reasons.first {
                    Label(reason, systemImage: "checkmark.circle.fill").font(.caption).foregroundStyle(Theme.accent)
                }
                Text(apartment.statusLabel).font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal, 18).padding(.bottom, 19).frame(maxWidth: .infinity, alignment: .leading).contentShape(Rectangle())
            }.buttonStyle(.plain).accessibilityIdentifier("apartment-open-\(apartment.id)")
        }
        .background(.background, in: .rect(cornerRadius: 24))
        .clipShape(.rect(cornerRadius: 24))
        .overlay(RoundedRectangle(cornerRadius: 24).strokeBorder(Theme.accent.opacity(0.08)))
        .accessibilityElement(children: .contain)
    }
}

struct ApartmentArtwork: View {
    let apartment: Apartment
    var body: some View {
        ZStack {
            Theme.secondary
            if let url = apartment.safeImageURL {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image): image.resizable().scaledToFit().padding(14)
                    case .failure: placeholder
                    default: ProgressView().tint(Theme.accent)
                    }
                }
            } else { placeholder }
        }.accessibilityLabel(apartment.safeImageURL == nil ? "Изображение не предоставлено" : "Изображение от застройщика")
    }
    private var placeholder: some View {
        VStack(spacing: 10) {
            Image(systemName: "building.2").font(.system(size: 44, weight: .ultraLight))
            Text(apartment.isDemo ? "Демонстрационный объект" : "Изображение не предоставлено").font(.caption)
        }.foregroundStyle(Theme.accent.opacity(0.65))
    }
}
