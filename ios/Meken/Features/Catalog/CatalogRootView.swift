import SwiftUI
import SwiftData
import MekenCore

struct CatalogRootView: View {
    @Environment(CatalogStore.self) private var model
    @Environment(AppStore.self) private var session
    @Environment(\.modelContext) private var context
    @AppStorage("catalog-onboarded") private var onboarded = false
    @State private var recovering = false
    @State private var initialized = false
    var body: some View {
        VStack(spacing: 0) {
            if !onboarded { CatalogOnboarding { onboarded = true } }
            else {
                if let error = model.error {
                    HStack { Text(error).font(.caption); Spacer(); Button("Повторить") { Task { await model.initialize() } } }.padding(12).background(Theme.sage)
                }
                if model.offline { Text("Сохранённые сведения. Подключитесь к сервису для нового поиска.").font(.caption).padding(8) }
                if model.ready && !model.supported && [.profile, .help].contains(model.page) {
                    Text("Каталог ЖК пока недоступен на подключённом сервере.").font(.caption).padding(12).frame(maxWidth: .infinity, alignment: .leading).background(Theme.sage)
                }
                page.frame(maxWidth: .infinity, maxHeight: .infinity)
                if model.tabsVisible { tabs }
            }
        }.background(.white).foregroundStyle(Theme.ink).buttonStyle(.plain)
        .task {
            guard !initialized else { return }; initialized = true
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("--catalog-onboarding") { onboarded = false }
            #endif
            await session.initializeCatalog(context: context); await model.initialize()
        }
        .sheet(isPresented: $recovering, onDismiss: { Task { await model.synchronizeIdentity() } }) { AccountRecoveryView() }
    }
    @ViewBuilder private var page: some View {
        if model.ready && !model.supported && ![.profile, .help].contains(model.page) {
            VStack(alignment: .leading, spacing: 20) {
                CatalogHeader(title: model.page == .favorites ? "Избранное" : "Каталог ЖК", back: model.tabsVisible ? nil : { model.back() }) {}
                Text("Каталог ЖК пока недоступен на подключённом сервере.").font(.subheadline).padding(.horizontal, 16)
                Button("Повторить подключение") { Task { await model.initialize() } }.buttonStyle(PrimaryButtonStyle()).disabled(model.busy).padding(.horizontal, 16)
                Spacer()
            }.accessibilityIdentifier("screen-catalog-unavailable")
        } else {
        switch model.page {
        case .search: CatalogSearchScreen()
        case .filters: CatalogFiltersScreen(initial: model.criteria)
        case .results: CatalogResultsScreen()
        case .map: CatalogMapScreen()
        case .conversation: CatalogConversationScreen()
        case .project: CatalogProjectScreen()
        case .layout: CatalogLayoutScreen()
        case .comparison: CatalogComparisonScreen()
        case .favorites: CatalogFavoritesScreen()
        case .history: CatalogHistoryScreen()
        case .profile: CatalogProfileScreen(recover: { recovering = true })
        case .help: CatalogHelpScreen()
        case .sources: CatalogSourcesScreen()
        case .report: CatalogReportScreen()
        }
        }
    }
    private var tabs: some View {
        HStack(spacing: 0) {
            tab("Поиск", symbol: "magnifyingglass", value: 0)
            tab("Избранное", symbol: "heart", value: 1)
            tab("Профиль", symbol: "person", value: 2)
        }.padding(.top, 8).padding(.bottom, 5).background(.white).overlay(alignment: .top) { Rectangle().fill(Theme.border).frame(height: 1) }
    }
    private func tab(_ title: String, symbol: String, value: Int) -> some View {
        Button { model.tab(value); if value == 1 { Task { await model.loadFavorites() } } } label: {
            VStack(spacing: 4) { Image(systemName: model.selectedTab == value && value == 2 ? "person.fill" : symbol).font(.system(size: 20)); Text(title).font(.system(size: 11)); Rectangle().fill(model.selectedTab == value ? Theme.accent : .clear).frame(width: 42, height: 2) }.frame(maxWidth: .infinity, minHeight: 44).foregroundStyle(model.selectedTab == value ? Theme.accent : Theme.muted)
        }.accessibilityIdentifier("catalog-tab-\(value)").accessibilityAddTraits(model.selectedTab == value ? .isSelected : [])
    }
}
struct CatalogOnboarding: View {
    @Environment(CatalogStore.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var revealed = false
    let begin: () -> Void
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ReferenceRegion(asset: "CatalogOnboarding", rect: CGRect(x: 0, y: 0, width: 852, height: 888), sourceSize: CGSize(width: 852, height: 1846), fill: true, topAligned: true)
                    .frame(height: 360)
                    .scaleEffect(revealed || reduceMotion ? 1 : 1.035)
                    .opacity(revealed || reduceMotion ? 1 : 0)
                    .clipped()
                    .animation(reduceMotion ? nil : .easeOut(duration: 0.9), value: revealed)
                VStack(alignment: .leading, spacing: 20) {
                    Text("ЖК разных\nзастройщиков\nв одном месте").font(.system(.title, weight: .bold)).fixedSize(horizontal: false, vertical: true)
                        .modifier(OnboardingEntrance(revealed: revealed, reduceMotion: reduceMotion, delay: 0.12))
                    Text("Ищите, сравнивайте и сохраняйте ЖК.\nСведения о ЖК — из открытых источников.").font(.subheadline).foregroundStyle(Theme.muted)
                        .modifier(OnboardingEntrance(revealed: revealed, reduceMotion: reduceMotion, delay: 0.22))
                    VStack(alignment: .leading, spacing: 8) { Text("Город поиска").font(.subheadline); city }
                        .modifier(OnboardingEntrance(revealed: revealed, reduceMotion: reduceMotion, delay: 0.32))
                    Button("Начать поиск", action: begin).buttonStyle(PrimaryButtonStyle()).disabled(!model.ready || model.criteria.city == nil)
                        .modifier(OnboardingEntrance(revealed: revealed, reduceMotion: reduceMotion, delay: 0.42))
                    if model.busy { ProgressView("Подключаемся к каталогу").font(.caption) }
                    if model.config?.isDemo == true { Text("Демонстрационный каталог").font(.caption).foregroundStyle(Theme.muted) }
                    if let error = model.error { Text(error).font(.caption); Button("Повторить") { Task { await model.initialize() } } }
                }.padding(24).background(.white, in: UnevenRoundedRectangle(topLeadingRadius: 20, topTrailingRadius: 20)).padding(.top, -14)
            }
        }.accessibilityIdentifier("screen-onboarding")
            .task { revealed = true }
    }
    private var city: some View { Menu { ForEach(model.cities, id: \.self) { city in Button(city) { model.chooseCity(city) } } } label: { CatalogRow(title: "", value: model.criteria.city ?? "Выберите город") } }
}
private struct OnboardingEntrance: ViewModifier {
    let revealed: Bool
    let reduceMotion: Bool
    let delay: Double

    func body(content: Content) -> some View {
        content
            .opacity(revealed || reduceMotion ? 1 : 0)
            .offset(y: revealed || reduceMotion ? 0 : 16)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.54).delay(delay), value: revealed)
    }
}
struct CatalogSearchScreen: View {
    @Environment(CatalogStore.self) private var model
    var body: some View {
        @Bindable var model = model
        VStack(spacing: 0) {
            CatalogHeader { Button { model.push(.history); Task { await model.loadHistory() } } label: { Label("История", systemImage: "clock.arrow.circlepath").font(.caption).foregroundStyle(Theme.muted) } }
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Поиск ЖК").font(.system(.title2, weight: .bold)).padding(.bottom, 3)
                    Menu { ForEach(model.cities, id: \.self) { city in Button(city) { model.chooseCity(city) } } } label: { CatalogRow(title: "", value: model.criteria.city ?? "Выберите город", symbol: "mappin.and.ellipse") }
                    HStack(spacing: 10) { Image(systemName: "magnifyingglass").foregroundStyle(Theme.muted); TextField("Название ЖК или район", text: Binding(get: { model.criteria.q ?? "" }, set: { model.criteria.q = $0.isEmpty ? nil : $0 })).font(.subheadline).submitLabel(.search).onSubmit { Task { await model.search() } } }.padding(13).background(Theme.secondary, in: .rect(cornerRadius: 9))
                    Button { model.push(.filters) } label: { CatalogRow(title: "Цена ЖК «от»", value: model.criteria.priceSummary, symbol: "banknote") }
                    Button { model.push(.filters) } label: { CatalogRow(title: "Район", value: model.criteria.districts.isEmpty ? "Все районы" : model.criteria.districts.joined(separator: ", "), symbol: "building.2") }
                    HStack(spacing: 8) {
                        Button { if model.criteria.stages.contains("commissioned") { model.criteria.stages.removeAll { $0 == "commissioned" } } else { model.criteria.stages = ["commissioned"] } } label: { HStack { Image(systemName: "graduationcap"); Text("Сданные ЖК"); Image(systemName: model.criteria.stages.contains("commissioned") ? "checkmark.circle.fill" : "circle") }.font(.caption).padding(10).frame(maxWidth: .infinity).background(Theme.secondary, in: .rect(cornerRadius: 7)) }
                        Button { model.push(.conversation) } label: { Label("Школа рядом", systemImage: "building.2").font(.caption).padding(10).frame(maxWidth: .infinity).background(Theme.secondary, in: .rect(cornerRadius: 7)) }
                    }
                    Button { model.push(.filters) } label: { CatalogRow(title: "", value: "Все фильтры", symbol: "slider.horizontal.3") }.accessibilityIdentifier("catalog-filters")
                    Button { Task { await model.search() } } label: { Label(model.busy ? "Ищем ЖК" : "Найти ЖК", systemImage: "magnifyingglass") }.buttonStyle(PrimaryButtonStyle()).disabled(model.busy || !model.supported || model.offline)
                    Button { model.push(.conversation) } label: {
                        HStack(spacing: 12) { Image(systemName: "bubble.left.and.bubble.right"); VStack(alignment: .leading, spacing: 2) { Text("Уточнить в разговоре").font(.subheadline); Text("Опишите, что важно для вас").font(.caption).foregroundStyle(Theme.muted) }; Spacer(); Image(systemName: "chevron.right").font(.caption) }.padding(12).background(Theme.sage, in: .rect(cornerRadius: 9))
                    }.accessibilityLabel("Уточнить в разговоре").accessibilityHint("Опишите, что важно для вас").disabled(!model.supported || model.offline).padding(.top, 4)
                    if let recent = model.conversations.first {
                        Text("Последний поиск").font(.caption).foregroundStyle(Theme.muted).padding(.top, 10)
                        Button { Task { await model.restore(recent) } } label: { HStack { Image(systemName: "clock.arrow.circlepath"); Text(recent.title); Spacer(); Text("Продолжить").font(.caption2) }.font(.subheadline).padding(12).background(Theme.secondary, in: .rect(cornerRadius: 9)) }
                    }
                    Button { model.push(.help) } label: { HStack { Image(systemName: "info.circle"); Text("О каталоге и источниках"); Spacer(); Image(systemName: "chevron.right") }.font(.caption).foregroundStyle(Theme.muted).frame(minHeight: 44) }.padding(.top, 10)
                    if model.config?.isDemo == true { Text("Демо-каталог. Это не реальные предложения.").font(.caption2).foregroundStyle(Theme.muted) }
                    if !model.supported && model.ready { Text("Каталог ЖК пока недоступен на подключённом сервере.").font(.caption) }
                }.padding(14)
            }
        }.accessibilityIdentifier("screen-search")
    }
}
