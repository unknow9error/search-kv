import SwiftUI
import MekenCore

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var confirmingDelete = false
    @State private var deleting = false
    @State private var error: String?
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 15) {
                        BrandMark(size: 48)
                        VStack(alignment: .leading, spacing: 5) {
                            Text("meken").font(.title2.weight(.semibold))
                            Text("Место для вашей жизни").font(.footnote).foregroundStyle(.secondary)
                        }.padding(.vertical, 8)
                    }
                }
                Section("Как устроен подбор") {
                    Text("Вы рассказываете о пожеланиях. Приложение ищет по каталогу и постепенно уточняет предложения у застройщиков.")
                    Text("Наличие и цена могут измениться. Проверка в карточке запрашивает новый ответ, но не бронирует квартиру.")
                    if store.config?.aiEnabled != true {
                        Text("Сейчас включён базовый подбор: он понимает простые условия. Для сложных пожеланий используйте фильтры.")
                    }
                }.font(.subheadline)
                Section {
                    Text("Вход не нужен. Подборки привязаны к защищённой сессии на этом устройстве. Перенос между устройствами пока недоступен.")
                    Text("Сообщения и пожелания хранятся на сервере до \(store.config?.retentionDays ?? 90) дней с последней активности. Если ИИ включён, текст запросов передаётся поставщику ИИ. Не отправляйте ИИН, документы или платёжные данные.")
                    if let url = Apartment.httpsURL(store.config?.privacyUrl) { Link("Политика конфиденциальности", destination: url) }
                    if let url = Apartment.httpsURL(store.config?.termsUrl) { Link("Условия использования", destination: url) }
                } header: { Text("Ваши данные") }
                Section {
                    Button("Удалить мои данные", role: .destructive) { confirmingDelete = true }.disabled(deleting)
                    if deleting { ProgressView("Удаляю данные") }
                    if let error { Text(error).font(.footnote).foregroundStyle(.red) }
                } footer: { Text("Будут удалены диалоги, пожелания, избранное и текущая сессия. После этого можно начать заново.") }
                Section { LabeledContent("Версия", value: "1.0 (1)") }
            }
            .navigationTitle("О приложении")
            .confirmationDialog("Удалить все мои данные?", isPresented: $confirmingDelete, titleVisibility: .visible) {
                Button("Удалить без восстановления", role: .destructive) {
                    deleting = true
                    Task {
                        do { try await store.deleteAccount() }
                        catch { self.error = store.friendly(error) }
                        deleting = false
                    }
                }
                Button("Отмена", role: .cancel) {}
            } message: { Text("Диалоги, пожелания и избранное будут удалены с сервера и этого устройства.") }
        }
    }
}
