import SwiftUI

struct AccountRecoveryView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    @State private var recoveryCode = ""
    @State private var confirmingRecovery = false
    @State private var isRecovering = false
    @State private var isVisible = false
    @State private var currentScenePhase: ScenePhase = .inactive
    @State private var error: String?
    @State private var recoveryTask: Task<Void, Never>?
    @FocusState private var codeFocused: Bool

    private var normalizedCode: String { recoveryCode.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var validCode: Bool { !normalizedCode.isEmpty && normalizedCode.unicodeScalars.count <= 128 }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text("Введите код, который вы сохранили в настройках Meken. Откроются подборки и избранное этого профиля.")
                        .font(.subheadline)
                    SecureField("Код восстановления", text: $recoveryCode)
                        .textContentType(.password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.asciiCapable)
                        .privacySensitive()
                        .focused($codeFocused)
                        .disabled(isRecovering)
                        .submitLabel(.go)
                        .onSubmit { confirmRecovery() }
                } footer: {
                    Text("Никому не сообщайте этот код. Он даёт доступ к вашим подборкам и избранному.")
                }
                Section {
                    Button("Открыть сохранённый профиль") { confirmRecovery() }
                        .disabled(!validCode || isRecovering || store.isDeleting)
                    if isRecovering { ProgressView("Открываю профиль") }
                    if let error { Text(error).font(.footnote).foregroundStyle(.red) }
                } footer: {
                    Text("Если код утерян, его нельзя восстановить. На устройстве с открытым профилем можно создать новый код в настройках.")
                }
            }
            .scrollContentBackground(.hidden).background(Theme.paper)
            .navigationTitle("Восстановление профиля")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Отмена") {
                        clearRecoveryCode()
                        dismiss()
                    }.disabled(isRecovering)
                }
            }
            .interactiveDismissDisabled(isRecovering)
            .confirmationDialog("Открыть сохранённый профиль?", isPresented: $confirmingRecovery, titleVisibility: .visible) {
                Button("Открыть профиль") { recoverAccount() }
                Button("Отмена", role: .cancel) {}
            } message: {
                Text("На этом устройстве откроется профиль, которому принадлежит код. Текущие подборки и избранное не будут объединены с ним. Данные текущего профиля не будут удалены с сервера.")
            }
            .onAppear {
                isVisible = true
                currentScenePhase = scenePhase
            }
            .onDisappear {
                isVisible = false
                clearRecoveryCode()
                recoveryTask?.cancel()
            }
            .onChange(of: scenePhase) {
                currentScenePhase = scenePhase
                if scenePhase != .active {
                    clearRecoveryCode()
                    confirmingRecovery = false
                    codeFocused = false
                }
                if scenePhase == .background { recoveryTask?.cancel() }
            }
        }
    }

    private func confirmRecovery() {
        guard validCode, !isRecovering, !store.isDeleting else { return }
        codeFocused = false
        confirmingRecovery = true
    }

    private func recoverAccount() {
        guard validCode, !isRecovering, !store.isDeleting else { return }
        let code = normalizedCode
        error = nil
        isRecovering = true
        recoveryTask = Task { @MainActor in
            defer {
                isRecovering = false
                recoveryTask = nil
            }
            do {
                try await store.recoverAccount(code: code)
                clearRecoveryCode()
                guard !Task.isCancelled, isVisible else { return }
                dismiss()
            } catch {
                guard !Task.isCancelled, isVisible, currentScenePhase == .active else { return }
                self.error = store.friendly(error)
            }
        }
    }

    private func clearRecoveryCode() { recoveryCode = "" }
}
