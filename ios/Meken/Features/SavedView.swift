import SwiftUI
import MekenCore

struct SavedView: View {
    @Environment(AppStore.self) private var store
    @State private var selected: Apartment?
    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 20) {
                    if let error = store.favoriteError { NoticeCard(text: error, symbol: "wifi.exclamationmark") }
                    ForEach(store.favorites) { apartment in
                        ApartmentCard(apartment: apartment) { selected = apartment }
                    }
                }.padding(22)
            }
            .refreshable { await store.loadFavorites() }
            .overlay { if store.favorites.isEmpty { EmptyState(symbol: "heart", title: "Квартиры, к которым хочется вернуться", detail: "Нажмите на сердечко в подборке. Сохранённые варианты появятся здесь.") } }
            .navigationTitle("Избранное").background(Theme.paper)
            .sheet(item: $selected) { ApartmentDetailView(initialApartment: $0) }
        }
    }
}

