import SwiftUI
import SwiftData

@main
struct MekenApp: App {
    @State private var store: AppStore
    @State private var catalog: CatalogStore
    private let modelContainer: ModelContainer?

    init() {
        #if DEBUG
        let raw = Bundle.main.object(forInfoDictionaryKey: "MekenAPIBaseURL") as? String ?? ""
        let url = URL(string: raw) ?? URL(string: "https://configuration.meken.invalid")!
        #else
        let url = URL(string: "https://194.238.43.134")!
        #endif
        let api = APIClient(baseURL: url)
        _store = State(initialValue: AppStore(api: api))
        _catalog = State(initialValue: CatalogStore(api: api))
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
                CatalogRootView().environment(catalog).environment(store).tint(Theme.accent).modelContainer(modelContainer).preferredColorScheme(.light)
            } else {
                ContentUnavailableView("Не удалось подготовить приложение", systemImage: "externaldrive.badge.exclamationmark", description: Text("Перезапустите приложение. Если ошибка повторится, проверьте свободное место на устройстве."))
            }
        }
    }
}
