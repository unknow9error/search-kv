import SwiftUI
import LocalAuthentication
import UIKit
import UniformTypeIdentifiers
import MekenCore

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var confirmingDelete = false
    @State private var confirmingRecoveryCode = false
    @State private var showingRecovery = false
    @State private var showingHistory = false
    @State private var deleting = false
    @State private var error: String?
    @State private var recoveryError: String?
    @State private var recoveryCode: String?
    @State private var copiedRecoveryCode = false
    @State private var isGeneratingCode = false
    @State private var isVisible = false
    @State private var currentScenePhase: ScenePhase = .inactive
    @State private var codeGenerationID: UUID?
    @State private var pendingGenerationID: UUID?
    @State private var generationTask: Task<Void, Never>?
    @State private var authenticationContext: LAContext?

    private var isBusy: Bool { deleting || store.isDeleting || isGeneratingCode }
    private var version: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        guard let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String else { return version }
        return "\(version) (\(build))"
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Профиль").font(.system(.title, weight: .bold)).foregroundStyle(Theme.ink)
                    NoticeCard(text: "Подборки и избранное доступны без регистрации.", symbol: "person.crop.circle")
                    SurfaceGroup(title: "Ваши данные") {
                        Button { store.selectedTab = 1 } label: { profileRow("Избранное", symbol: "heart") }
                        Divider()
                        Button {
                            Task { await store.refreshHistory(); showingHistory = true }
                        } label: { profileRow("История поиска", symbol: "clock.arrow.circlepath") }
                    }
                    if store.supportsAccountRecovery { recoverySection }
                    NavigationLink { HelpView() } label: {
                        SearchFieldRow(title: "", value: "Помощь и документы", symbol: "doc.text")
                    }
                    SurfaceGroup(title: "Управление данными") {
                        Button("Удалить мои данные", role: .destructive) { confirmingDelete = true }.disabled(isBusy).frame(minHeight: 44)
                        if deleting { ProgressView("Удаляю данные") }
                        if let error { Text(error).font(.footnote).foregroundStyle(.red) }
                        Text("Будут удалены диалоги, пожелания, избранное и текущая сессия. После этого можно начать заново.").font(.caption).foregroundStyle(Theme.muted)
                    }
                    Text("Версия \(version)").font(.caption).foregroundStyle(Theme.muted)
                }.padding(.horizontal, 22).padding(.vertical, 18)
            }
            .buttonStyle(.plain).background(Theme.paper)
            .toolbarBackground(Theme.paper, for: .navigationBar)
            .toolbarVisibility(.hidden, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showingHistory) { HistoryView() }
            .sheet(isPresented: $showingRecovery) { AccountRecoveryView() }
            .confirmationDialog("Создать новый код восстановления?", isPresented: $confirmingRecoveryCode, titleVisibility: .visible) {
                Button("Создать код") { generateRecoveryCode() }
                Button("Отмена", role: .cancel) {}
            } message: {
                Text("Прежний код перестанет работать. Сохраните новый в надёжном месте: любой, у кого он есть, сможет открыть ваши подборки и избранное.")
            }
            .confirmationDialog("Удалить все мои данные?", isPresented: $confirmingDelete, titleVisibility: .visible) {
                Button("Удалить без восстановления", role: .destructive) {
                    clearRecoveryCode()
                    error = nil
                    deleting = true
                    Task {
                        do { try await store.deleteAccount() }
                        catch { self.error = store.friendly(error) }
                        deleting = false
                    }
                }
                Button("Отмена", role: .cancel) {}
            } message: { Text("Диалоги, пожелания и избранное будут удалены с сервера и этого устройства.") }
            .onAppear {
                isVisible = true
                currentScenePhase = scenePhase
            }
            .onDisappear {
                isVisible = false
                clearRecoveryCode()
                cancelCodeGeneration()
            }
            .onChange(of: scenePhase) {
                currentScenePhase = scenePhase
                if scenePhase != .active { clearRecoveryCode() }
                if scenePhase == .background { cancelCodeGeneration() }
                if scenePhase == .active { issueRecoveryCodeIfActive() }
            }
            .onChange(of: store.isDeleting) {
                if store.isDeleting { clearRecoveryCode() }
            }
            .onChange(of: store.requiresSessionReset) {
                if store.requiresSessionReset { clearRecoveryCode() }
            }
        }
    }

    private func profileRow(_ title: String, symbol: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol).foregroundStyle(Theme.accent).frame(width: 22)
            Text(title).font(.subheadline)
            Spacer()
            Image(systemName: "chevron.right").font(.caption).foregroundStyle(Theme.muted)
        }.frame(minHeight: 36).contentShape(Rectangle())
    }

    private var recoverySection: some View {
        SurfaceGroup(title: "На другом устройстве") {
            Text("Сохраните код, чтобы открыть этот профиль на другом устройстве.").font(.subheadline).foregroundStyle(Theme.muted)
            Button("Создать код восстановления") { confirmingRecoveryCode = true }.buttonStyle(PrimaryButtonStyle())
                .disabled(isBusy || store.requiresSessionReset || !store.isReady)
            if isGeneratingCode { ProgressView("Подготавливаю код") }
            if let recoveryCode {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Сохраните код сейчас. Позже приложение не сможет показать его снова.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text(recoveryCode)
                        .font(.system(.body, design: .monospaced))
                        .fixedSize(horizontal: false, vertical: true)
                        .privacySensitive()
                        .accessibilityLabel("Код восстановления")
                        .accessibilityValue(recoveryCode)
                    Button(copiedRecoveryCode ? "Код скопирован" : "Скопировать код") {
                        UIPasteboard.general.setItems(
                            [[UTType.utf8PlainText.identifier: recoveryCode]],
                            options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)]
                        )
                        copiedRecoveryCode = true
                    }
                    if copiedRecoveryCode {
                        Text("Код будет удалён из буфера обмена через 2 минуты.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button("Скрыть код") { clearRecoveryCode() }
                }
            }
            if let recoveryError { Text(recoveryError).font(.footnote).foregroundStyle(.red) }
            Button("Восстановить профиль по коду") {
                clearRecoveryCode()
                showingRecovery = true
            }.disabled(isBusy).frame(maxWidth: .infinity, minHeight: 44)
            Text("Любой, у кого есть код, может открыть ваши подборки и избранное. Новый код заменяет прежний. Храните его в менеджере паролей и не отправляйте другим людям.")
                .font(.caption).foregroundStyle(Theme.muted)
        }
    }

    private func generateRecoveryCode() {
        guard !isBusy, currentScenePhase == .active, store.supportsAccountRecovery, !store.requiresSessionReset, store.isReady else { return }
        clearRecoveryCode()
        recoveryError = nil
        let context = LAContext()
        context.localizedCancelTitle = "Отмена"
        var authenticationError: NSError?
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &authenticationError) else {
            recoveryError = "Чтобы создать код восстановления, включите код-пароль устройства в настройках iPhone."
            return
        }
        let operationID = UUID()
        codeGenerationID = operationID
        authenticationContext = context
        isGeneratingCode = true
        generationTask = Task { @MainActor in
            do {
                let authenticated = try await context.evaluatePolicy(
                    .deviceOwnerAuthentication,
                    localizedReason: "Подтвердите создание кода доступа к вашим подборкам и избранному."
                )
                guard authenticated, !Task.isCancelled, isVisible, codeGenerationID == operationID else {
                    finishCodeGeneration(operationID)
                    return
                }
            } catch {
                if !Task.isCancelled, isVisible, codeGenerationID == operationID, let error = error as? LAError,
                   ![.userCancel, .appCancel, .systemCancel].contains(error.code) {
                    recoveryError = "Не удалось подтвердить владельца устройства. Повторите попытку."
                }
                finishCodeGeneration(operationID)
                return
            }
            authenticationContext = nil
            generationTask = nil
            pendingGenerationID = operationID
            issueRecoveryCodeIfActive()
        }
    }

    private func issueRecoveryCodeIfActive() {
        guard let operationID = pendingGenerationID, codeGenerationID == operationID,
              isVisible, currentScenePhase == .active else { return }
        pendingGenerationID = nil
        guard store.supportsAccountRecovery, !store.requiresSessionReset, !store.isDeleting, store.isReady else {
            finishCodeGeneration(operationID)
            return
        }
        generationTask = Task { @MainActor in
            defer { finishCodeGeneration(operationID) }
            do {
                let code = try await store.generateRecoveryCode()
                guard !Task.isCancelled, isVisible, codeGenerationID == operationID else { return }
                guard currentScenePhase == .active else {
                    recoveryError = "Код создан, но не показан, пока приложение неактивно. Создайте новый код и сохраните его."
                    return
                }
                recoveryCode = code
            } catch {
                guard !Task.isCancelled, isVisible, codeGenerationID == operationID else { return }
                recoveryError = store.friendly(error)
            }
        }
    }

    private func finishCodeGeneration(_ operationID: UUID) {
        guard codeGenerationID == operationID else { return }
        authenticationContext = nil
        generationTask = nil
        pendingGenerationID = nil
        codeGenerationID = nil
        isGeneratingCode = false
    }

    private func clearRecoveryCode() {
        recoveryCode = nil
        copiedRecoveryCode = false
    }

    private func cancelCodeGeneration() {
        generationTask?.cancel()
        authenticationContext?.invalidate()
        if let operationID = codeGenerationID { finishCodeGeneration(operationID) }
    }
}

struct HelpView: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        Form {
            Section("О каталоге") {
                Text("Ищите квартиры по городу, бюджету и количеству комнат. Сохраните понравившиеся варианты или уточните пожелания в разговоре.")
                if store.config?.aiEnabled != true {
                    Text("Сейчас включён базовый подбор: он понимает простые условия. Для сложных пожеланий используйте фильтры.")
                }
                DisclosureGroup("Что означают источник и дата?") {
                    Text("Источник ведёт к сведениям застройщика. Дата в карточке показывает, когда приложение получило эти сведения.")
                }
                DisclosureGroup("Можно ли забронировать квартиру?") {
                    Text("Проверка в карточке запрашивает новый ответ, но не бронирует квартиру. Наличие и цена могут измениться. Для покупки откройте первоисточник.")
                }
                DisclosureGroup("Почему в карточке нет сведений?") {
                    Text("В подключённом каталоге могут отсутствовать изображение, инфраструктура или другие сведения. Отсутствие данных не означает отсутствие объекта.")
                }
            }.listRowBackground(Theme.secondary)
            Section("Документы и данные") {
                if let url = Apartment.httpsURL(store.config?.privacyUrl) { Link("Политика конфиденциальности", destination: url) }
                if let url = Apartment.httpsURL(store.config?.termsUrl) { Link("Условия использования", destination: url) }
                Text("Сообщения и пожелания хранятся на сервере до \(store.config?.retentionDays ?? 90) дней с последней активности. Если ИИ включён, текст запросов передаётся поставщику ИИ. Не отправляйте ИИН, документы или платёжные данные.")
            }.listRowBackground(Theme.secondary)
        }
        .font(.subheadline)
        .scrollContentBackground(.hidden).background(Theme.paper)
        .toolbarVisibility(.visible, for: .navigationBar)
        .navigationTitle("Помощь и документы").navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Theme.paper, for: .navigationBar)
    }
}
