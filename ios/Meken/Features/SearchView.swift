import SwiftUI
import MekenCore

struct SearchView: View {
    @Environment(AppStore.self) private var store
    @State private var draft = ""
    @State private var showingFilters = false
    @State private var showingConversation = false
    @State private var searchCity = ""
    @State private var formError: String?
    @State private var showingHistory = false
    @State private var showingHelp = false
    @State private var selectedApartment: Apartment?
    @State private var olderMessagesAnchor: UUID?
    @FocusState private var composerFocused: Bool

    var body: some View {
        NavigationStack {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 22) {
                        if store.config?.isDemo == true {
                            NoticeCard(text: "Демо-каталог. Квартиры не продаются.", symbol: "sparkles.rectangle.stack")
                        }
                        if store.hasOlderMessages {
                            Button {
                                olderMessagesAnchor = store.search.messages.first?.id
                                Task {
                                    await store.loadOlderMessages()
                                    if store.historyLoadError != nil { olderMessagesAnchor = nil }
                                }
                            } label: {
                                HStack {
                                    if store.isLoadingOlderMessages { ProgressView() }
                                    Text(store.isLoadingOlderMessages ? "Загружаю сообщения" : "Показать предыдущие сообщения")
                                }.font(.subheadline.weight(.medium))
                            }.disabled(store.isLoadingOlderMessages || store.isPreparingSearch || store.search.isStreaming || store.requiresSessionReset || store.isDeleting)
                        }
                        if let error = store.historyLoadError { NoticeCard(text: error, symbol: "wifi.exclamationmark") }
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
                                if store.search.turnID != nil || store.pendingRequest != nil || store.pendingConversation != nil {
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
                .onChange(of: store.search.messages.last?.id) {
                    if let message = store.search.messages.last, message.isUser { proxy.scrollTo(message.id, anchor: .top) }
                }
                .onChange(of: store.search.messages.first?.id) {
                    if let anchor = olderMessagesAnchor, store.search.messages.contains(where: { $0.id == anchor }) {
                        proxy.scrollTo(anchor, anchor: .top)
                    }
                    olderMessagesAnchor = nil
                }
                .safeAreaInset(edge: .bottom, spacing: 0) {
                    if showingConversation || !store.search.messages.isEmpty { composer }
                }
            }
            .background(Theme.paper)
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarVisibility(.hidden, for: .navigationBar)
            .safeAreaInset(edge: .top, spacing: 0) {
                HStack(spacing: 12) {
                    BrandWordmark().fixedSize()
                    Spacer(minLength: 8)
                    Button {
                        Task { await store.refreshHistory(); showingHistory = true }
                    } label: { Label("История", systemImage: "clock.arrow.circlepath").font(.caption).foregroundStyle(Theme.muted) }
                    .buttonStyle(.plain).frame(minHeight: 44)
                    Button("Новый подбор", systemImage: "square.and.pencil") { store.newConversation(); showingConversation = false }
                        .labelStyle(.iconOnly).buttonStyle(.plain).frame(width: 44, height: 44).foregroundStyle(Theme.accent)
                }.padding(.horizontal, 22).padding(.vertical, 3).background(Theme.paper)
            }
            .onAppear { syncCity() }
            .onChange(of: store.config?.cities) { syncCity() }
            .onChange(of: store.search.preferences.city) { syncCity(force: true) }
            .sheet(isPresented: $showingFilters) { FiltersView(initialCity: searchCity.isEmpty ? nil : searchCity) }
            .sheet(isPresented: $showingHistory) { HistoryView() }
            .sheet(isPresented: $showingHelp) {
                NavigationStack { HelpView().toolbar { ToolbarItem(placement: .cancellationAction) { Button("Закрыть") { showingHelp = false } } } }
            }
            .sheet(item: $selectedApartment) { ApartmentDetailView(initialApartment: $0) }
            .alert("Не удалось изменить избранное", isPresented: Binding(get: { store.favoriteError != nil && !store.favoritesOffline }, set: { if !$0 { store.favoriteError = nil } })) {
                Button("Понятно") { store.favoriteError = nil }
            } message: { Text(store.favoriteError ?? "") }
        }
    }

    private var searchBusy: Bool {
        store.search.isStreaming || store.search.isPending || store.isPreparingSearch || store.requiresSessionReset || store.isDeleting || store.pendingRequest != nil || store.pendingConversation != nil
    }

    private func syncCity(force: Bool = false) {
        guard force || searchCity.isEmpty || store.config?.cities.contains(searchCity) != true else { return }
        searchCity = store.search.preferences.city ?? (store.config?.cities.contains("Астана") == true ? "Астана" : store.config?.cities.first ?? "")
    }

    private var welcome: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Поиск квартир").font(.system(.title, weight: .bold)).foregroundStyle(Theme.ink).padding(.bottom, 6)
            Menu {
                ForEach(store.config?.cities ?? [], id: \.self) { city in
                    Button(city) { searchCity = city }
                }
            } label: {
                SearchFieldRow(title: "", value: searchCity.isEmpty ? "Выберите город" : searchCity, symbol: "mappin.and.ellipse")
            }.disabled(searchBusy)
            Button { showingFilters = true } label: {
                SearchFieldRow(title: "Бюджет квартиры", value: budgetLabel, symbol: "banknote")
            }.buttonStyle(.plain).disabled(searchBusy)
            Button { showingFilters = true } label: {
                SearchFieldRow(title: "Комнаты", value: store.search.preferences.rooms.isEmpty ? "Любое количество" : store.search.preferences.rooms.sorted().map(String.init).joined(separator: ", "), symbol: "square.split.2x2")
            }.buttonStyle(.plain).disabled(searchBusy)
            HStack(spacing: 8) {
                suggestionButton("Астана до 35 млн ₸")
                Button { showingConversation = true; draft = searchCity.isEmpty ? "Нужна школа рядом" : "Ищу квартиру. Город: \(searchCity). Нужна школа рядом"; composerFocused = true } label: {
                    Label("Школа рядом", systemImage: "graduationcap").font(.caption).padding(12)
                        .foregroundStyle(Theme.ink).background(Theme.secondary, in: .rect(cornerRadius: 8))
                }.buttonStyle(.plain).disabled(searchBusy)
            }
            Button { showingFilters = true } label: {
                SearchFieldRow(title: "", value: "Все фильтры", symbol: "slider.horizontal.3")
            }.buttonStyle(.plain).disabled(searchBusy)
            Button {
                var preferences = store.search.preferences
                preferences.city = searchCity.isEmpty ? nil : searchCity
                formError = nil
                Task {
                    do { try await store.applyFilters(preferences) }
                    catch is CancellationError {}
                    catch { formError = store.friendly(error) }
                }
            } label: {
                Label("Найти квартиры", systemImage: "magnifyingglass")
            }.buttonStyle(PrimaryButtonStyle()).disabled(searchBusy || searchCity.isEmpty)
            if let formError { NoticeCard(text: formError) }
            Button { showingConversation = true; draft = searchCity.isEmpty ? "" : "Ищу квартиру. Город: \(searchCity). "; composerFocused = true } label: {
                HStack(spacing: 12) {
                    Image(systemName: "bubble.left.and.bubble.right")
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Уточнить в разговоре").font(.subheadline.weight(.medium))
                        Text("Опишите, что важно для вас").font(.caption).foregroundStyle(Theme.muted)
                    }
                    Spacer()
                    Image(systemName: "chevron.right").font(.caption)
                }.foregroundStyle(Theme.ink).padding(14).background(Theme.sage, in: .rect(cornerRadius: 10))
            }.buttonStyle(.plain).disabled(searchBusy).padding(.top, 6)
            if let recent = store.conversations.first {
                Text("Последний поиск").font(.caption).foregroundStyle(Theme.muted).padding(.top, 12)
                Button {
                    Task {
                        do { try await store.restoreConversation(recent.id) }
                        catch is CancellationError {}
                        catch { formError = store.friendly(error) }
                    }
                } label: { SearchFieldRow(title: "", value: recent.title, symbol: "clock.arrow.circlepath") }
                .buttonStyle(.plain).disabled(searchBusy)
            }
            Button { showingHelp = true } label: {
                HStack(spacing: 8) { Image(systemName: "info.circle"); Text("О каталоге и источниках"); Spacer(); Image(systemName: "chevron.right") }
                    .font(.caption).foregroundStyle(Theme.muted).frame(minHeight: 44)
            }.buttonStyle(.plain).padding(.top, 8)
        }
    }

    private var budgetLabel: String {
        guard let budget = store.search.preferences.budgetMax else { return "Без ограничений" }
        return "До \((Double(budget) / 1_000_000).formatted(.number.precision(.fractionLength(0...2)))) млн ₸"
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
                .foregroundStyle(Theme.ink).background(Theme.secondary, in: .rect(cornerRadius: 8))
        }.disabled(store.search.isStreaming || store.isPreparingSearch || store.pendingRequest != nil || store.pendingConversation != nil)
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
                    .disabled(store.isPreparingSearch || store.requiresSessionReset || store.isDeleting || store.search.isPending || store.pendingRequest != nil || store.pendingConversation != nil || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || draft.unicodeScalars.count > 2000)
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
            .padding(14)
            .foregroundStyle(Theme.ink)
            .background(message.isUser ? Theme.sage : Theme.secondary, in: .rect(cornerRadius: 12))
            if !message.isUser { Spacer(minLength: 8) }
        }
        .accessibilityElement(children: .contain)
    }
}

struct HistoryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var deletingConversation: ConversationSummary?
    @State private var deleteError: String?
    var body: some View {
        NavigationStack {
            List(store.conversations) { conversation in
                Button {
                    Task {
                        do { try await store.restoreConversation(conversation.id); dismiss() }
                        catch is CancellationError {}
                        catch { store.search.error = store.friendly(error); dismiss() }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(conversation.title).foregroundStyle(Theme.ink)
                        Text(conversation.preferences.city ?? "Город ещё не выбран").font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical, 6)
                }
                .swipeActions {
                    Button("Удалить", role: .destructive) { deletingConversation = conversation }
                }
            }
            .overlay { if store.conversations.isEmpty { EmptyState(symbol: "bubble.left.and.bubble.right", title: "Здесь будут ваши подборки", detail: "Начните разговор о квартире — он сохранится автоматически.") } }
            .scrollContentBackground(.hidden).background(Theme.paper)
            .navigationTitle("История").navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Готово") { dismiss() } } }
            .confirmationDialog("Удалить подборку?", isPresented: Binding(get: { deletingConversation != nil }, set: { if !$0 { deletingConversation = nil } }), titleVisibility: .visible) {
                if let conversation = deletingConversation {
                    Button("Удалить подборку", role: .destructive) {
                        Task {
                            do { try await store.deleteConversation(conversation.id) }
                            catch is CancellationError {}
                            catch { deleteError = store.friendly(error) }
                        }
                    }
                }
                Button("Отмена", role: .cancel) { deletingConversation = nil }
            } message: { Text("Диалог и его пожелания будут удалены с сервера. Избранные квартиры сохранятся.") }
            .alert("Не удалось удалить подборку", isPresented: Binding(get: { deleteError != nil }, set: { if !$0 { deleteError = nil } })) {
                Button("Понятно") { deleteError = nil }
            } message: { Text(deleteError ?? "") }
        }
    }
}
