import SwiftUI
import SwiftData

@main
struct MekenApp: App {
    @State private var store: AppStore
    private let modelContainer: ModelContainer?

    init() {
        let raw = Bundle.main.object(forInfoDictionaryKey: "MekenAPIBaseURL") as? String ?? ""
        let url = URL(string: raw) ?? URL(string: "https://configuration.meken.invalid")!
        _store = State(initialValue: AppStore(api: APIClient(baseURL: url)))
        do {
            modelContainer = try ModelContainer(for: CachedFavorite.self)
        } catch {
            // A cache failure must not erase remote favorites or prevent network access.
            let configuration = ModelConfiguration(isStoredInMemoryOnly: true)
            modelContainer = try? ModelContainer(for: CachedFavorite.self, configurations: configuration)
        }
    }
    var body: some Scene {
        WindowGroup {
            if let modelContainer {
                RootView().environment(store).tint(Theme.accent).modelContainer(modelContainer)
            } else {
                ContentUnavailableView("Не удалось подготовить приложение", systemImage: "externaldrive.badge.exclamationmark", description: Text("Перезапустите приложение. Если ошибка повторится, проверьте свободное место на устройстве."))
            }
        }
    }
}

struct RootView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.modelContext) private var context
    var body: some View {
        @Bindable var store = store
        Group {
            if store.isReady {
                TabView(selection: $store.selectedTab) {
                    Tab("Подбор", systemImage: "sparkle.magnifyingglass", value: 0) { SearchView() }
                    Tab("Избранное", systemImage: "heart", value: 1) { SavedView() }
                    Tab("О приложении", systemImage: "person.crop.circle", value: 2) { SettingsView() }
                }
            } else {
                GeometryReader { geometry in
                ScrollView {
                VStack(spacing: 22) {
                    BrandMark(size: 66)
                    Text("meken").font(.system(.largeTitle, design: .rounded, weight: .bold)).foregroundStyle(Theme.ink)
                    if let error = store.initializationError {
                        Text(error).font(.subheadline).multilineTextAlignment(.center).foregroundStyle(.secondary)
                        Button("Попробовать снова") { Task { await store.initialize(context: context) } }.buttonStyle(PrimaryButtonStyle())
                    } else {
                        ProgressView("Готовим ваш поиск").tint(Theme.accent)
                    }
                }.padding(36).frame(maxWidth: .infinity, minHeight: geometry.size.height)
                }
                }.background(Theme.paper)
            }
        }
        .task {
            await store.initialize(context: context)
            #if DEBUG
            if ProcessInfo.processInfo.arguments.contains("--smoke-search"), store.isReady {
                store.newConversation()
                store.send("Хочу 2-комнатную квартиру в Астане до 35 млн")
            }
            #endif
        }
    }
}
