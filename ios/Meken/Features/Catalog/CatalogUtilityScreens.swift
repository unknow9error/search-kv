import SwiftUI
import MekenCore

struct CatalogProfileScreen: View {
    @Environment(CatalogStore.self) private var model
    @Environment(AppStore.self) private var session
    @State private var confirmingDelete = false
    @State private var deleting = false
    let recover: () -> Void
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Профиль").font(.system(.title2, weight: .bold))
                Text("Подборки и избранное\nдоступны без регистрации.").font(.subheadline.weight(.medium)).foregroundStyle(Theme.accent).padding(14).frame(maxWidth: .infinity, alignment: .leading).background(Theme.sage, in: .rect(cornerRadius: 10))
                SurfaceGroup(title: "Ваши данные") {
                    Button { model.tab(1); Task { await model.loadFavorites() } } label: { row("Избранное", symbol: "heart") }
                    Button { model.push(.history); Task { await model.loadHistory() } } label: { row("История поиска", symbol: "magnifyingglass") }
                }
                if session.supportsAccountRecovery { CatalogRecoveryPanel(recovering: recover) }
                Button { model.push(.help) } label: { row("Помощь и документы", symbol: "doc.text").padding(13).background(Theme.secondary, in: .rect(cornerRadius: 10)) }
                SurfaceGroup(title: "Управление данными") {
                    Button("Удалить мои данные", role: .destructive) { confirmingDelete = true }.disabled(deleting || session.isDeleting).frame(minHeight: 44)
                    Text("Диалоги, условия и избранное будут удалены с сервера и этого устройства.").font(.caption).foregroundStyle(Theme.muted)
                    if deleting { ProgressView("Удаляем данные") }
                }
            }.padding(16)
        }.confirmationDialog("Удалить все мои данные?", isPresented: $confirmingDelete, titleVisibility: .visible) {
            Button("Удалить без восстановления", role: .destructive) { deleting = true; Task { do { try await session.deleteAccount(); try model.erasePrivateCache(); model.clearScope(); await model.initialize() } catch { model.error = model.friendly(error) }; deleting = false } }
            Button("Отмена", role: .cancel) {}
        } message: { Text("Все диалоги и избранные ЖК будут удалены. Восстановить удалённые сведения будет невозможно.") }.accessibilityIdentifier("screen-profile")
    }
    private func row(_ title: String, symbol: String) -> some View { HStack(spacing: 10) { Image(systemName: symbol).foregroundStyle(Theme.accent).frame(width: 22); Text(title).font(.subheadline); Spacer(); Image(systemName: "chevron.right").font(.caption).foregroundStyle(Theme.muted) }.frame(minHeight: 36).contentShape(Rectangle()) }
}
struct CatalogHelpScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var expanded = "source"
    var body: some View {
        VStack(spacing: 0) {
            CatalogHeader(title: "", back: model.back) { EmptyView() }
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Помощь и документы").font(.system(.title2, weight: .bold))
                    Text("Meken собирает сведения о ЖК из открытых источников.").font(.subheadline).foregroundStyle(Theme.muted)
                    SurfaceGroup(title: "О данных") {
                        answer("source", "Что означают источник и дата?", "Источник — ссылка на публикацию. Дата сбора — когда Meken прочитал её.")
                        if expanded == "source" { Button("Источники каталога") { model.push(.sources); Task { await model.loadSources() } }.font(.caption).underline() }
                        Divider()
                        answer("missing", "Почему в карточке нет сведений?", "Источник может не публиковать отдельные поля. Отсутствие данных в Meken не означает отсутствие объекта или документа.")
                        Divider()
                        answer("book", "Можно ли забронировать квартиру?", "Meken не бронирует квартиры и не передаёт заявки менеджерам. Откройте первоисточник, чтобы связаться с застройщиком.")
                    }
                    Button("Сообщить об ошибке в ЖК") { model.push(.report) }.buttonStyle(PrimaryButtonStyle()).disabled(model.config?.capabilities.contains("data_reports") != true)
                    SurfaceGroup(title: "Документы и данные") {
                        if let url = Apartment.httpsURL(model.config?.privacyUrl) { Link("Политика конфиденциальности", destination: url).font(.subheadline).frame(minHeight: 44) } else { Text("Политика конфиденциальности: ссылка не предоставлена").font(.caption) }
                        Divider()
                        if let url = Apartment.httpsURL(model.config?.termsUrl) { Link("Условия использования", destination: url).font(.subheadline).frame(minHeight: 44) } else { Text("Условия использования: ссылка не предоставлена").font(.caption) }
                        Divider()
                        Button("Управление моими данными") { model.tab(2) }.font(.subheadline).frame(minHeight: 44)
                    }
                    SurfaceGroup {
                        answer("coverage", "Все ли ЖК есть в каталоге?", "Каталог частичный. Публичные сведения, сроки и цены могут различаться между источниками.")
                        answer("price", "Что означает цена ЖК «от»?", "Это опубликованная стартовая цена проекта. Она не гарантирует бюджет конкретной квартиры. Минимум по наблюдавшимся лотам показывается отдельно.")
                        Text("Подборки хранятся до \(model.config?.retentionDays ?? 90) дней с последней активности. Не отправляйте ИИН, документы и платёжные данные.").font(.caption).foregroundStyle(Theme.muted)
                    }
                }.padding(16)
            }
        }.accessibilityIdentifier("screen-help")
    }
    private func answer(_ key: String, _ title: String, _ text: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Button { expanded = expanded == key ? "" : key } label: { HStack { Text(title).font(.caption.weight(.semibold)); Spacer(); Image(systemName: expanded == key ? "chevron.up" : "chevron.down").font(.caption) }.frame(minHeight: 38) }
            if expanded == key { Text(text).font(.caption).foregroundStyle(Theme.muted) }
        }
    }
}
struct CatalogSourcesScreen: View {
    @Environment(CatalogStore.self) private var model
    var body: some View {
        VStack(spacing: 0) {
            CatalogHeader(title: "Источники каталога", back: model.back) { EmptyView() }
            ScrollView { LazyVStack(alignment: .leading, spacing: 14) {
                if model.sources.isEmpty { Text("Нет сведений об источниках. Попробуйте обновить список.").font(.subheadline); Button("Обновить") { Task { await model.loadSources() } } }
                ForEach(model.sources) { source in SurfaceGroup(title: source.name) {
                    Text(source.provenance == "demo" ? "Демонстрационный источник" : source.cities.joined(separator: ", ")).font(.caption).foregroundStyle(Theme.muted)
                    if source.provenance != "demo", let url = Apartment.httpsURL(source.websiteUrl ?? source.sourceUrl) { Link("Открыть источник", destination: url).font(.subheadline) } else { Text("Ссылка недоступна").font(.caption) }
                } }
            }.padding(16) }
        }
    }
}
struct CatalogReportScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var selectedId = ""; @State private var category = "other"; @State private var message = ""
    @State private var reportId = UUID().uuidString; @State private var sending = false; @State private var sent = false
    var body: some View {
        VStack(spacing: 0) {
            CatalogHeader(title: "Сообщить об ошибке", back: model.back) { EmptyView() }
            ScrollView { VStack(alignment: .leading, spacing: 16) {
                if sent { Label("Сообщение получено", systemImage: "checkmark.circle").font(.headline); Text("Это квитанция о получении, а не обещание ответа или исправления.").font(.subheadline) }
                else {
                    Text("Выберите ЖК и опишите неточность. Не добавляйте личные данные.").font(.subheadline)
                    Picker("Жилой комплекс", selection: $selectedId) { Text("Выберите ЖК").tag(""); ForEach(projects) { Text($0.name).tag($0.id) } }.pickerStyle(.menu)
                    Picker("Что исправить", selection: $category) { Text("Цена").tag("price"); Text("Адрес").tag("address"); Text("Срок").tag("completion"); Text("Изображение").tag("image"); Text("Планировка").tag("layout"); Text("Другое").tag("other") }
                    TextField("Опишите неточность", text: $message, axis: .vertical).lineLimit(5...12).padding(12).background(Theme.secondary, in: .rect(cornerRadius: 9))
                    Button(sending ? "Отправляем" : "Отправить сообщение") { sending = true; Task { sent = await model.report(id: reportId, projectId: selectedId, category: category, message: message); sending = false } }.buttonStyle(PrimaryButtonStyle()).disabled(sending || selectedId.isEmpty || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || message.unicodeScalars.count > 2000)
                }
            }.padding(16) }
        }
    }
    private var projects: [CatalogProject] { var unique: [CatalogProject] = []; for project in ([model.project].compactMap { $0 } + model.results + model.favorites) where !unique.contains(where: { $0.id == project.id }) { unique.append(project) }; return unique }
}
