import SwiftUI
import MekenCore

struct SearchView: View {
    @Environment(AppStore.self) private var store
    @State private var draft = ""
    @State private var showingFilters = false
    @State private var showingHistory = false
    @State private var selectedApartment: Apartment?
    @FocusState private var composerFocused: Bool

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        if store.config?.isDemo == true {
                            NoticeCard(text: "Демонстрационный каталог. Эти квартиры показывают, как работает подбор, и не продаются.", symbol: "sparkles.rectangle.stack")
                        }
                        if store.search.messages.isEmpty { welcome }
                        if let notice = store.search.notice { NoticeCard(text: notice) }
                        ForEach(store.search.messages) { message in MessageView(message: message).id(message.id) }
                        if !store.search.apartments.isEmpty {
                            results
                        }
                        if store.search.isStreaming {
                            HStack(spacing: 12) {
                                ProgressView().tint(Theme.accent)
                                Text(store.search.status.isEmpty ? "Ищу подходящие варианты" : store.search.status).font(.footnote).foregroundStyle(.secondary)
                            }
                            .accessibilityElement(children: .combine)
                            .padding(.vertical, 8)
                        }
                        if let error = store.search.error {
                            VStack(alignment: .leading, spacing: 10) {
                                NoticeCard(text: error, symbol: "wifi.exclamationmark")
                                if store.search.turnID != nil {
                                    Button("Проверить сохранённый ответ") { Task { await store.resume() } }
                                        .font(.subheadline.weight(.medium)).tint(Theme.accent)
                                }
                            }
                        }
                        if !store.search.suggestions.isEmpty && !store.search.isStreaming {
                            ScrollView(.horizontal) {
                                HStack(spacing: 8) {
                                    ForEach(store.search.suggestions, id: \.self) { suggestion in
                                        suggestionButton(suggestion)
                                    }
                                }
                            }.scrollIndicators(.hidden)
                        }
                        Color.clear.frame(height: 1).id("latest")
                    }
                    .padding(.horizontal, 22).padding(.top, 10).padding(.bottom, 20)
                }
                .scrollDismissesKeyboard(.interactively)
                .onChange(of: store.search.messages.count) {
                    if let message = store.search.messages.last, message.isUser { proxy.scrollTo(message.id, anchor: .top) }
                }
                .safeAreaInset(edge: .bottom, spacing: 0) { composer }
            }
            .background(Theme.paper)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    HStack(spacing: 9) { BrandMark(); Text("meken").font(.system(.title2, design: .rounded, weight: .bold)).foregroundStyle(Theme.ink) }.fixedSize()
                }
                ToolbarItemGroup(placement: .topBarTrailing) {
                    Button("История", systemImage: "clock.arrow.circlepath") {
                        Task { await store.refreshHistory(); showingHistory = true }
                    }
                    Button("Новый подбор", systemImage: "square.and.pencil") { store.newConversation() }
                }
            }
            .sheet(isPresented: $showingFilters) { FiltersView() }
            .sheet(isPresented: $showingHistory) { HistoryView() }
            .sheet(item: $selectedApartment) { ApartmentDetailView(initialApartment: $0) }
            .alert("Не удалось изменить избранное", isPresented: Binding(get: { store.favoriteError != nil && !store.favoritesOffline }, set: { if !$0 { store.favoriteError = nil } })) {
                Button("Понятно") { store.favoriteError = nil }
            } message: { Text(store.favoriteError ?? "") }
        }
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack {
                Label("ВАШ СЛЕДУЮЩИЙ АДРЕС", systemImage: "location.north.circle")
                    .font(.system(.caption2, design: .monospaced, weight: .medium)).tracking(1.2).foregroundStyle(Theme.accent)
                Spacer()
            }.padding(.top, 18)
            Text("Найдём место,\nкоторое станет\nвашим.")
                .font(.system(.largeTitle, design: .serif, weight: .medium)).foregroundStyle(Theme.ink)
                .fixedSize(horizontal: false, vertical: true)
            Text("Не нужно разбираться во всём сразу. Расскажите, как вы хотите жить — начнём с простого.")
                .font(.subheadline).foregroundStyle(.secondary).lineSpacing(4)
            HouseIllustration().frame(height: 180).padding(.top, -5)
            VStack(alignment: .leading, spacing: 10) {
                Text("С ЧЕГО НАЧНЁМ?").font(.system(.caption2, design: .monospaced)).tracking(1.5).foregroundStyle(.secondary)
                Button { store.send("Хочу квартиру") } label: {
                    HStack { Image(systemName: "sparkles"); Text("Просто хочу квартиру"); Spacer(); Image(systemName: "arrow.up.right") }
                }.buttonStyle(PrimaryButtonStyle())
                HStack(spacing: 10) {
                    suggestionButton("Астана до 35 млн ₸")
                    suggestionButton("Нужна школа рядом")
                }
            }
            HStack(spacing: 7) {
                Image(systemName: "building.2.crop.circle")
                Text(store.config?.isDemo == true ? "Попробуйте поиск на примерах" : "Предложения напрямую из каталога застройщика")
            }.font(.caption).foregroundStyle(.secondary)
        }
    }

    private var results: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Ваша подборка").font(.title2.weight(.semibold))
                    Text("\(store.search.apartments.count) вариантов · \(store.search.preferences.city ?? "Все города")").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Фильтры", systemImage: "slider.horizontal.3") { showingFilters = true }.labelStyle(.iconOnly)
                    .frame(width: 44, height: 44).background(Theme.secondary, in: .circle)
            }
            ForEach(store.search.apartments) { apartment in
                VStack(spacing: 8) {
                    ApartmentCard(apartment: apartment) { selectedApartment = apartment }
                    Button {
                        if store.selectedForComparison.contains(apartment.id) { store.selectedForComparison.remove(apartment.id) }
                        else if store.selectedForComparison.count < 3 { store.selectedForComparison.insert(apartment.id) }
                    } label: {
                        Label(store.selectedForComparison.contains(apartment.id) ? "Добавлена к сравнению" : "Сравнить", systemImage: store.selectedForComparison.contains(apartment.id) ? "checkmark.circle.fill" : "plus.circle")
                            .font(.footnote).frame(minHeight: 36)
                    }.disabled(!store.selectedForComparison.contains(apartment.id) && store.selectedForComparison.count >= 3)
                }
            }
            if store.selectedForComparison.count >= 2 {
                Button("Сравнить выбранные (\(store.selectedForComparison.count))") { store.compare() }
                    .buttonStyle(PrimaryButtonStyle()).disabled(store.search.isStreaming)
            }
        }
    }

    private func suggestionButton(_ text: String) -> some View {
        Button { store.send(text) } label: {
            Text(text).font(.footnote.weight(.medium)).padding(.horizontal, 14).padding(.vertical, 12)
                .foregroundStyle(Theme.ink).background(Theme.secondary, in: .rect(cornerRadius: 14))
        }.disabled(store.search.isStreaming)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            Button("Фильтры", systemImage: "slider.horizontal.3") { showingFilters = true }
                .labelStyle(.iconOnly).frame(width: 44, height: 46).foregroundStyle(Theme.accent)
            TextField("Например, двушка рядом со школой", text: $draft, axis: .vertical)
                .font(.subheadline).lineLimit(1...5).focused($composerFocused)
                .padding(.vertical, 14).accessibilityLabel("Ваши пожелания к квартире")
                .overlay(alignment: .topTrailing) {
                    if draft.unicodeScalars.count > 1900 { Text("\(draft.unicodeScalars.count)/2000").font(.caption2).foregroundStyle(draft.unicodeScalars.count > 2000 ? .red : .secondary).offset(y: -14) }
                }
            if store.search.isStreaming {
                Button("Остановить подбор", systemImage: "stop.fill") { store.stop() }
                    .labelStyle(.iconOnly).frame(width: 46, height: 46).foregroundStyle(.white).background(Theme.accent, in: .circle)
            } else {
                Button {
                    let message = draft; draft = ""; composerFocused = false; store.send(message)
                } label: { Image(systemName: "arrow.up").font(.system(size: 19, weight: .semibold)).frame(width: 46, height: 46) }
                    .foregroundStyle(.white).background(Theme.accent, in: .circle)
                    .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draft.unicodeScalars.count > 2000)
                    .opacity(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? 0.4 : 1)
                    .accessibilityLabel("Отправить сообщение")
            }
        }
        .padding(8).background(.background, in: .rect(cornerRadius: 28))
        .overlay(RoundedRectangle(cornerRadius: 28).strokeBorder(Theme.accent.opacity(0.12)))
        .padding(.horizontal, 16).padding(.vertical, 10).background(Theme.paper)
    }
}

struct MessageView: View {
    let message: ChatMessage
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            if message.isUser { Spacer(minLength: 36) }
            else { BrandMark(size: 26).padding(.top, 4) }
            VStack(alignment: .leading, spacing: 12) {
                Text(message.text).font(.subheadline).lineSpacing(4).textSelection(.enabled)
                ForEach(message.citations) { citation in
                    if let url = citation.safeURL {
                        Link(destination: url) { Label(citation.title, systemImage: "arrow.up.right.square").font(.caption) }
                    } else if !citation.demo {
                        Label(citation.title, systemImage: "book.closed").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            .padding(message.isUser ? 15 : 0)
            .foregroundStyle(message.isUser ? .white : Theme.ink)
            .background(message.isUser ? Theme.accent : .clear, in: .rect(cornerRadius: 20))
            if !message.isUser { Spacer(minLength: 8) }
        }
        .accessibilityElement(children: .contain)
    }
}

struct HistoryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            List(store.conversations) { conversation in
                Button {
                    Task {
                        do { try await store.restoreConversation(conversation.id); dismiss() }
                        catch { store.search.error = store.friendly(error); dismiss() }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(conversation.title).foregroundStyle(Theme.ink)
                        Text(conversation.preferences.city ?? "Город ещё не выбран").font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 6)
                }
            }
            .overlay { if store.conversations.isEmpty { EmptyState(symbol: "bubble.left.and.bubble.right", title: "Здесь будут ваши подборки", detail: "Начните разговор о квартире — он сохранится автоматически.") } }
            .navigationTitle("История").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Готово") { dismiss() } } }
        }
    }
}
