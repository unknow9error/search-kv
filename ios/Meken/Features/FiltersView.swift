import SwiftUI
import MekenCore

struct FiltersView: View {
    var initialCity: String? = nil
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var preferences = Preferences()
    @State private var budget = ""
    @State private var isSaving = false
    @State private var error: String?
    private let amenities = [("school", "Школа"), ("kindergarten", "Детский сад"), ("park", "Парк"), ("transit", "Остановка")]
    var body: some View {
        NavigationStack {
            Form {
                Section("Где ищем") {
                    Picker("Город", selection: Binding(get: { preferences.city ?? "" }, set: { preferences.city = $0.isEmpty ? nil : $0 })) {
                        Text("Выберите город").tag("")
                        ForEach(store.config?.cities ?? [], id: \.self) { Text($0).tag($0) }
                    }
                }
                Section {
                    TextField("Например, 35", text: $budget).keyboardType(.decimalPad)
                        .accessibilityLabel("Максимальная стоимость в миллионах тенге")
                } header: { Text("Общий бюджет, млн ₸") } footer: { Text("Полная стоимость квартиры. Оставьте пустым, если пока не определились.") }
                Section("Количество комнат") {
                    ScrollView(.horizontal) {
                        HStack(spacing: 8) {
                            ForEach(1...5, id: \.self) { count in
                                Button {
                                    if preferences.rooms.contains(count) { preferences.rooms.removeAll { $0 == count } }
                                    else { preferences.rooms.append(count) }
                                } label: {
                                    Text("\(count)").font(.subheadline.weight(.medium)).frame(minWidth: 44, minHeight: 44)
                                        .foregroundStyle(preferences.rooms.contains(count) ? .white : Theme.ink)
                                        .background(preferences.rooms.contains(count) ? Theme.accent : Theme.secondary, in: .rect(cornerRadius: 8))
                                }.buttonStyle(.plain).accessibilityLabel("\(count) комнат")
                                .accessibilityAddTraits(preferences.rooms.contains(count) ? .isSelected : [])
                            }
                        }
                    }.scrollIndicators(.hidden)
                }
                Section {
                    ForEach(amenities, id: \.0) { kind, title in
                        Picker(title, selection: Binding(get: { preferences.requiredAmenities.contains(kind) ? 2 : preferences.preferredAmenities.contains(kind) ? 1 : 0 }, set: { priority in
                            preferences.requiredAmenities.removeAll { $0 == kind }
                            preferences.preferredAmenities.removeAll { $0 == kind }
                            if priority == 1 { preferences.preferredAmenities.append(kind) }
                            if priority == 2 { preferences.requiredAmenities.append(kind) }
                        })) {
                            Text("Не важно").tag(0)
                            Text("Желательно").tag(1)
                            Text("Обязательно").tag(2)
                        }
                    }
                    Picker("Где искать инфраструктуру", selection: $preferences.amenityScope) {
                        Text("Рядом по расстоянию").tag("nearby")
                        Text("В этом ЖК").tag("complex")
                        Text("В бигвилле").tag("bigville")
                    }
                    if preferences.amenityScope == "nearby" {
                    Picker("Радиус", selection: $preferences.amenityRadiusM) {
                        Text("500 м").tag(500); Text("1 км").tag(1000); Text("2 км").tag(2000)
                    }
                    }
                } header: { Text("Что должно быть рядом") } footer: { Text("Для поиска рядом используются расстояния по прямой; для ЖК и бигвилля — сведения застройщика. «Обязательно» требует подтверждения действующего объекта. Запланированная школа не считается работающей.") }
                if let error { Section { Text(error).foregroundStyle(.red) } }

            }
            .scrollContentBackground(.hidden).background(Theme.paper)
            .safeAreaInset(edge: .bottom, spacing: 0) {
                Button(action: apply) {
                    HStack { Text("Показать квартиры"); if isSaving { ProgressView().tint(.white) } }
                }.buttonStyle(PrimaryButtonStyle())
                .disabled(isSaving || store.isPreparingSearch || store.search.isStreaming || store.search.isPending || store.pendingRequest != nil || store.pendingConversation != nil || store.requiresSessionReset || store.isDeleting)
                .padding(16).background(Theme.paper)
            }
            .navigationTitle("Фильтры").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("Сбросить") { preferences = Preferences(); budget = ""; error = nil } }
            }
            .onAppear {
                preferences = store.search.preferences
                if let initialCity { preferences.city = initialCity }
                budget = preferences.budgetMax.map { String(Double($0) / 1_000_000) } ?? ""
            }
        }.tint(Theme.accent)
    }

    private func apply() {
        let value = budget.trimmingCharacters(in: .whitespacesAndNewlines).replacingOccurrences(of: ",", with: ".")
        if !value.isEmpty {
            guard let millions = Double(value), millions.isFinite, millions >= 1, millions <= 10000 else { error = "Укажите бюджет от 1 до 10 000 млн ₸."; return }
            preferences.budgetMax = Int((millions * 1_000_000).rounded())
        } else { preferences.budgetMax = nil }
        isSaving = true
        Task {
            do { try await store.applyFilters(preferences); dismiss() }
            catch is CancellationError {}
            catch { self.error = store.friendly(error) }
            isSaving = false
        }
    }
}
