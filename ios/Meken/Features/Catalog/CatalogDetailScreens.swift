import SwiftUI
import MekenCore

struct CatalogProjectScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var section = "Обзор"
    @State private var refreshing = false
    var body: some View {
        if let project = model.project {
            VStack(spacing: 0) {
                CatalogHeader(title: project.name, back: model.back) {
                    Button { Task { await model.favorite(project) } } label: { Image(systemName: model.favorites.contains(where: { $0.id == project.id }) ? "heart.fill" : "heart").frame(width: 34, height: 44) }.accessibilityLabel("Сохранить ЖК")
                    Button { model.select(project.id); if model.selected.count >= 2 { Task { await model.compare() } } } label: { Label("Сравнить", systemImage: "chart.bar").font(.caption) }
                }
                ScrollView {
                    VStack(alignment: .leading, spacing: 14) {
                        CatalogArtwork(project: project).frame(height: 225).clipShape(.rect(cornerRadius: 9))
                        Text(project.name).font(.system(.title2, weight: .bold))
                        Text([project.developerName, project.city, project.district.isEmpty ? nil : project.district].compactMap { $0 }.joined(separator: " · ")).font(.caption).foregroundStyle(Theme.muted)
                        HStack(alignment: .top, spacing: 12) {
                            VStack(alignment: .leading, spacing: 5) { Text(project.displayPrice.kind == "observed_listing_minimum" ? "Минимум по опубликованным лотам" : "Цена ЖК «от»").font(.caption).foregroundStyle(Theme.muted); Text(project.displayPrice.label).font(.headline).monospacedDigit() }.frame(maxWidth: .infinity, alignment: .leading)
                            VStack(alignment: .leading, spacing: 5) { Text(project.buildings.first?.name ?? "Корпус не опубликован").font(.caption.weight(.semibold)); Text(project.stageLabel).font(.caption); Text("Заявленный срок").font(.caption2).foregroundStyle(Theme.muted); Text(project.completion ?? "Не опубликован").font(.caption) }.frame(maxWidth: .infinity, alignment: .leading)
                        }.padding(12).background(Theme.secondary, in: .rect(cornerRadius: 9))
                        Text(project.sourceLabel).font(.caption).foregroundStyle(Theme.muted).frame(maxWidth: .infinity, alignment: .trailing)
                        HStack(spacing: 4) { ForEach(["Обзор", "Планировки", "Документы"], id: \.self) { name in Button { section = name } label: { Text(name).font(.caption).frame(maxWidth: .infinity).padding(.vertical, 10).foregroundStyle(section == name ? .white : Theme.ink).background(section == name ? Theme.accent : Theme.secondary, in: .rect(cornerRadius: 7)) } } }
                        if section == "Обзор" {
                            if let address = project.address { Text(address).font(.subheadline) }
                            HStack { Text("Отделка"); Spacer(); Text(project.finish ?? "Не опубликована") }.font(.subheadline).padding(.vertical, 5)
                            ForEach(project.buildings) { building in HStack { Text(building.name); Spacer(); Text(building.completion ?? "Срок не опубликован") }.font(.caption) }
                            Text("Сведения относятся к публичным публикациям. Цена «от» и опубликованные лоты не подтверждают наличие квартиры для покупки.").font(.caption).foregroundStyle(Theme.muted)
                            Button { model.push(.conversation) } label: { Label("Уточнить в разговоре", systemImage: "bubble.left.and.bubble.right").font(.subheadline) }
                        } else if section == "Планировки" {
                            if project.layouts.isEmpty { Text("Нет данных о опубликованных планировках.").font(.subheadline).foregroundStyle(Theme.muted) }
                            ForEach(project.layouts) { layout in Button { model.openLayout(layout) } label: { CatalogRow(title: "Тип планировки", value: layout.parameterLabel, symbol: "square.split.2x2") } }
                        } else {
                            if project.documents.isEmpty { Text("Нет данных о документах. Это не означает отсутствие разрешений у застройщика.").font(.subheadline).foregroundStyle(Theme.muted) }
                            ForEach(project.documents) { document in if let url = Apartment.httpsURL(document.url), !project.isDemo { Link(destination: url) { CatalogRow(title: "Документ", value: document.name, symbol: "doc.text") } } else { Text(document.name + " · ссылка недоступна").font(.subheadline) } }
                        }
                    }.padding(14)
                }
                sourceButton(project.sourceURL, demo: project.isDemo).padding(14)
            }.accessibilityIdentifier("screen-project")
        }
    }
    private func sourceButton(_ url: URL?, demo: Bool) -> some View {
        Group { if let url { Link(destination: url) { Label("Открыть первоисточник", systemImage: "arrow.up.right.square").frame(maxWidth: .infinity) }.buttonStyle(PrimaryButtonStyle()) } else { VStack(spacing: 6) { Button("Открыть первоисточник") {}.buttonStyle(PrimaryButtonStyle()).disabled(true).opacity(0.45); Text(demo ? "Первоисточник недоступен в демо-каталоге" : "Ссылка на первоисточник не предоставлена").font(.caption2).foregroundStyle(Theme.muted) } } }
    }
}
struct CatalogLayoutScreen: View {
    @Environment(CatalogStore.self) private var model
    var body: some View {
        if let layout = model.layout {
            VStack(spacing: 0) {
                CatalogHeader(title: "Планировка", back: model.back) { if let project = model.project { Button { Task { await model.favorite(project) } } label: { Image(systemName: "heart").frame(width: 44, height: 44) }.accessibilityLabel("Сохранить ЖК") } }
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        Text(model.project?.name ?? layout.name).font(.subheadline.weight(.semibold)); Text(model.project?.buildings.first?.name ?? "Тип планировки").font(.caption).foregroundStyle(Theme.muted)
                        if layout.provenance == "demo", layout.externalId.hasPrefix("design-") {
                            ReferenceRegion(asset: "CatalogDemoLayout", rect: CGRect(x: 90, y: 432, width: 689, height: 712), sourceSize: CGSize(width: 852, height: 1846)).frame(height: 340)
                        } else if let raw = layout.imageUrl, let url = Apartment.httpsURL(raw) {
                            AsyncImage(url: url) { phase in if let image = phase.image { image.resizable().scaledToFit() } else if phase.error != nil { Text("Не удалось загрузить изображение") } else { ProgressView() } }.frame(minHeight: 260)
                        } else { EmptyState(symbol: "square.split.2x2", title: "Изображение не опубликовано", detail: "Откройте первоисточник, чтобы уточнить сведения.") }
                        Text("Тип планировки").font(.caption).foregroundStyle(Theme.muted); Text(layout.parameterLabel).font(.headline)
                        Label("Цена не опубликована", systemImage: "tag").font(.subheadline).padding(12).frame(maxWidth: .infinity, alignment: .leading).background(Theme.secondary, in: .rect(cornerRadius: 9))
                        Text("Это тип планировки. Цена, этаж и наличие конкретной квартиры не подтверждаются.").font(.caption).foregroundStyle(Theme.muted)
                        HStack { Image(systemName: "globe"); Text(model.project?.providerName ?? "Источник"); Spacer(); Image(systemName: "chevron.right") }.font(.caption)
                        HStack { Label("Собрано \(CatalogProject.dateLabel(layout.observedAt))", systemImage: "calendar"); Spacer(); Button("Обновить сведения") { Task { await refresh() } }.disabled(model.busy) }.font(.caption2).foregroundStyle(Theme.muted)
                    }.padding(18)
                }
                if layout.provenance != "demo", let url = Apartment.httpsURL(layout.sourceUrl) { Link(destination: url) { Label("Открыть первоисточник", systemImage: "arrow.up.right.square") }.buttonStyle(PrimaryButtonStyle()).padding(18) }
                else { VStack(spacing: 6) { Button("Открыть первоисточник") {}.buttonStyle(PrimaryButtonStyle()).disabled(true).opacity(0.45); Text("Демонстрационная планировка").font(.caption2).foregroundStyle(Theme.muted) }.padding(18) }
            }.accessibilityIdentifier("screen-layout")
        }
    }
    private func refresh() async {
        guard let layout = model.layout else { return }
        do { let updated: ProjectLayout = try await model.api.call("v1/project-layouts/" + layout.id); model.layout = updated }
        catch { model.error = model.friendly(error) }
    }
}
struct CatalogComparisonScreen: View {
    @Environment(CatalogStore.self) private var model
    var body: some View {
        VStack(spacing: 0) {
            CatalogHeader(title: "Сравнение ЖК", back: model.back) { Text("\(model.comparison?.projects.count ?? 0) ЖК").font(.caption) }
            if let comparison = model.comparison {
                ScrollView([.vertical, .horizontal]) {
                    VStack(alignment: .leading, spacing: 14) {
                        HStack(alignment: .top, spacing: 10) { ForEach(comparison.projects) { project in VStack(alignment: .leading, spacing: 9) { CatalogArtwork(project: project).frame(height: 125).clipShape(.rect(cornerRadius: 8)); Text(project.name).font(.subheadline.weight(.bold)); Text(project.developerName ?? "Застройщик не опубликован").font(.caption); Text(project.sourceLabel).font(.caption2).foregroundStyle(Theme.muted); Button("Открыть ЖК") { model.openProject(project) }.buttonStyle(PrimaryButtonStyle()) }.frame(width: 165) } }
                        comparisonGroup("Цена ЖК «от»", projects: comparison.projects) { $0.publishedStartingPrice?.label ?? "Цена не опубликована" }
                        comparisonGroup("Корпус и срок сдачи", projects: comparison.projects) { ($0.buildings.first?.name ?? "Корпус не опубликован") + "\n" + ($0.buildings.first?.completion ?? $0.completion ?? "Срок не опубликован") }
                        comparisonGroup("Отделка", projects: comparison.projects) { $0.finish ?? "Не опубликована" }
                        DisclosureGroup("Дополнительные сведения") {
                            ForEach(comparison.rows.filter { !["published_starting_price", "completion", "finish"].contains($0.key) }) { row in
                                comparisonGroup(row.label, projects: comparison.projects) { project in row.key == "stage" ? project.stageLabel : value(row, project: project) }
                            }
                        }.font(.caption)
                    }.padding(16)
                }
                if comparison.projects.count == 3 { Text("Прокрутите вправо, чтобы посмотреть третий ЖК").font(.caption).foregroundStyle(Theme.muted).padding(10) }
            }
        }.accessibilityIdentifier("screen-comparison")
    }
    private func comparisonGroup(_ title: String, projects: [CatalogProject], value: @escaping (CatalogProject) -> String) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Text(title).font(.caption.weight(.semibold))
            HStack(alignment: .top, spacing: 10) { ForEach(projects) { project in Text(value(project)).font(.caption).fixedSize(horizontal: false, vertical: true).frame(width: 165, alignment: .leading) } }
        }.padding(12).background(Theme.secondary, in: .rect(cornerRadius: 9))
    }
    private func value(_ row: ProjectComparisonRow, project: CatalogProject) -> String {
        let value = row.values.first { $0.projectId == project.id }?.value
        if case .number(let number) = value, row.key.contains("price") { return "от \(ProjectCriteria.millions(Int(number))) млн ₸" }
        return value?.label ?? "Не опубликовано"
    }
}
struct CatalogFavoritesScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var choosing = false
    var body: some View {
        VStack(spacing: 0) {
            HStack { Text("Избранное").font(.system(.title2, weight: .bold)); Spacer(); Button(choosing ? "Отмена" : "Выбрать") { choosing.toggle(); if !choosing { model.selected = [] } }.font(.caption) }.padding(16)
            ScrollView { LazyVStack(alignment: .leading, spacing: 12) {
                Text(choosing ? "Выберите до 3 ЖК." : "Сохранённые жилые комплексы").font(.caption).foregroundStyle(Theme.muted)
                if model.favorites.isEmpty { EmptyState(symbol: "heart", title: "Пока нет сохранённых ЖК", detail: "Нажмите на сердечко в карточке ЖК.") }
                ForEach(model.favorites) { CatalogProjectCard(project: $0, selectable: choosing) }
            }.padding(.horizontal, 14) }.refreshable { await model.loadFavorites() }
            if choosing { Button("Сравнить \(model.selected.count) ЖК") { Task { await model.compare() } }.buttonStyle(PrimaryButtonStyle()).disabled(model.selected.count < 2).padding(14) }
        }.accessibilityIdentifier("screen-favorites")
    }
}
struct CatalogHistoryScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var deleting: ProjectConversation?
    var body: some View {
        VStack(spacing: 0) {
            CatalogHeader(title: "История", back: model.back) { Button { model.newSearch() } label: { Label("Новый поиск", systemImage: "plus").font(.caption).padding(9).background(Theme.sage, in: .capsule) } }
            ScrollView { LazyVStack(alignment: .leading, spacing: 12) {
                Text("Сохранённые поиски").font(.caption).foregroundStyle(Theme.muted).padding(.top, 10)
                if model.conversations.isEmpty { EmptyState(symbol: "clock", title: "История пока пуста", detail: "Подборки из разговора сохраняются здесь.") }
                ForEach(model.conversations) { conversation in HStack(spacing: 12) {
                    Button { Task { await model.restore(conversation) } } label: { HStack(spacing: 10) { Image(systemName: "magnifyingglass"); VStack(alignment: .leading, spacing: 5) { Text(conversation.title).font(.subheadline.weight(.semibold)); Text("\(conversation.criteria.city ?? "Все города") · \(conversation.criteria.priceModeLabel) \(conversation.criteria.priceSummary.lowercased())").font(.caption).foregroundStyle(Theme.muted) } }.frame(maxWidth: .infinity, alignment: .leading) }
                    Button { deleting = conversation } label: { Image(systemName: "ellipsis").frame(width: 36, height: 44) }.accessibilityLabel("Управление поиском \(conversation.title)")
                }.padding(12).background(Theme.secondary, in: .rect(cornerRadius: 9)) }
            }.padding(16) }.refreshable { await model.loadHistory() }
        }.confirmationDialog("Удалить поиск?", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }), titleVisibility: .visible) {
            Button("Удалить поиск", role: .destructive) { if let deleting { Task { await model.deleteConversation(deleting.id) } }; deleting = nil }
            Button("Отмена", role: .cancel) { deleting = nil }
        } message: { Text("Сообщения и условия поиска будут удалены. Избранные ЖК сохранятся.") }.accessibilityIdentifier("screen-history")
    }
}
struct CatalogConversationScreen: View {
    @Environment(CatalogStore.self) private var model
    @State private var draft = ""
    var body: some View {
        VStack(spacing: 0) {
            CatalogHeader(title: "Разговор", back: model.back) { EmptyView() }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach([model.criteria.city, model.criteria.districts.first, model.criteria.priceMax == nil ? nil : model.criteria.priceModeLabel + " " + model.criteria.priceSummary.lowercased()].compactMap { $0 }, id: \.self) { criterion in Button { model.push(.filters) } label: { HStack { Text(criterion).font(.caption); Spacer(); Image(systemName: "pencil").font(.caption) }.padding(10).background(Theme.secondary, in: .rect(cornerRadius: 8)) } }
                    if model.historyTurns.isEmpty { Text("Уточните пожелания. Базовый помощник использует сведения каталога; сложные условия задайте через фильтры.").font(.caption).foregroundStyle(Theme.muted) }
                    if model.historyCursor != nil, let conversation = model.conversations.first(where: { $0.id == model.conversationId }) { Button("Предыдущие сообщения") { Task { await model.restore(conversation, more: true) } } }
                    ForEach(model.historyTurns) { turn in
                        HStack { Spacer(minLength: 45); Text(turn.message).font(.subheadline).padding(12).background(Theme.sage, in: .rect(cornerRadius: 12)) }
                        if let reply = turn.response {
                            HStack(alignment: .top, spacing: 8) { BrandMark(size: 26); Text(reply.message).font(.subheadline).padding(12).background(Theme.secondary, in: .rect(cornerRadius: 12)) }
                            ForEach(reply.results?.items.prefix(2) ?? [].prefix(2)) { CatalogProjectCard(project: $0, compact: true) }
                        } else { Text("Ответ не завершён. Повторите запрос.").font(.caption).foregroundStyle(Theme.muted) }
                    }
                    if let pending = model.pending { Text(pending.message).font(.subheadline).padding(12).background(Theme.sage, in: .rect(cornerRadius: 12)); Button("Повторить отправку") { Task { await model.send(pending.message, retry: true) } }.disabled(model.busy) }
                    if model.busy { ProgressView("Подбираем ЖК").font(.caption) }
                    if !model.suggestions.isEmpty { ScrollView(.horizontal) { HStack { ForEach(model.suggestions, id: \.self) { text in CatalogChip(title: text) { Task { await model.send(text) } } } } } }
                }.padding(14)
            }.scrollDismissesKeyboard(.interactively)
            HStack(spacing: 10) { TextField("Уточнить пожелания", text: $draft, axis: .vertical).font(.subheadline).lineLimit(1...5); Button { let message = draft; draft = ""; Task { await model.send(message) } } label: { Image(systemName: "arrow.up.right").frame(width: 36, height: 36).foregroundStyle(.white).background(Theme.accent, in: .circle) }.disabled(model.busy || model.pending != nil || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draft.unicodeScalars.count > 2000).accessibilityLabel("Отправить") }.padding(12).overlay(RoundedRectangle(cornerRadius: 9).stroke(Theme.border)).padding(14)
        }.accessibilityIdentifier("screen-conversation")
    }
}
