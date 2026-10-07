import SwiftUI
import LocalAuthentication
import UIKit
import UniformTypeIdentifiers
import MekenCore

struct CatalogRecoveryPanel: View {
    let recovering: () -> Void
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
    var body: some View {
        recoverySection
        .confirmationDialog("Создать новый код восстановления?", isPresented: $confirmingRecoveryCode, titleVisibility: .visible) {
            Button("Создать код") { generateRecoveryCode() }; Button("Отмена", role: .cancel) {}
        } message: { Text("Прежний код перестанет работать. Любой, у кого есть новый код, сможет открыть ваши подборки и избранное.") }
        .onAppear { isVisible = true; currentScenePhase = scenePhase }
        .onDisappear { isVisible = false; clearRecoveryCode(); cancelCodeGeneration() }
        .onChange(of: scenePhase) { currentScenePhase = scenePhase; if scenePhase != .active { clearRecoveryCode() }; if scenePhase == .background { cancelCodeGeneration() }; if scenePhase == .active { issueRecoveryCodeIfActive() } }
        .onChange(of: store.isDeleting) { if store.isDeleting { clearRecoveryCode() } }
        .onChange(of: store.requiresSessionReset) { if store.requiresSessionReset { clearRecoveryCode() } }
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
            Button("Восстановить по коду") {
                clearRecoveryCode()
                recovering()
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
