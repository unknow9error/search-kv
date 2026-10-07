package kz.unknown.meken.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.AtomicFile
import java.io.File
import java.io.FileNotFoundException
import java.security.KeyStore
import java.security.MessageDigest
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.TokenPair
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString

internal fun storageHash(value: String): String = MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

interface PrivateRecords {
    fun read(name: String): String?
    fun write(name: String, value: String)
    fun delete(name: String)
    fun deleteUser(userId: String)
}

/** Keystore keys never leave AndroidKeyStore. Files are excluded from backup and device transfer. */
class EncryptedFileStore(context: Context, endpoint: String) : PrivateRecords {
    private val namespace = storageHash(endpoint)
    private val directory = File(context.noBackupFilesDir, "meken-$namespace")
    private val alias = "kz.unknown.meken.$namespace"

    @Synchronized private fun key(): SecretKey {
        val keys = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keys.getKey(alias, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true).build())
        }.generateKey()
    }

    private fun file(name: String): AtomicFile {
        require(name.matches(Regex("[a-z0-9.-]+")))
        if (!directory.exists() && !directory.mkdirs()) throw StorageFailure()
        return AtomicFile(File(directory, name))
    }

    @Synchronized override fun read(name: String): String? {
        try {
            val encrypted = file(name).readFully()
            require(encrypted.size >= 29 && encrypted[0] == 1.toByte())
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, encrypted.copyOfRange(1, 13)))
            // Bind ciphertext to its logical file so account and token records cannot be swapped.
            cipher.updateAAD((namespace + ":" + name).toByteArray(Charsets.UTF_8))
            return cipher.doFinal(encrypted.copyOfRange(13, encrypted.size)).toString(Charsets.UTF_8)
        } catch (_: FileNotFoundException) {
            return null
        } catch (_: Exception) {
            throw StorageFailure()
        }
    }

    @Synchronized override fun write(name: String, value: String) {
        try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key())
            cipher.updateAAD((namespace + ":" + name).toByteArray(Charsets.UTF_8))
            val encrypted = byteArrayOf(1) + cipher.iv + cipher.doFinal(value.toByteArray(Charsets.UTF_8))
            val target = file(name)
            val output = target.startWrite()
            try {
                output.write(encrypted)
                target.finishWrite(output)
            } catch (error: Exception) {
                target.failWrite(output)
                throw error
            }
        } catch (_: Exception) {
            throw StorageFailure()
        }
    }

    @Synchronized override fun delete(name: String) { file(name).delete() }
    override fun deleteUser(userId: String) {
        val user = storageHash(userId)
        delete("favorites.$user")
        delete("pending.$user")
        delete("conversation.$user")
        delete("catalog.favorites.$user")
        delete("catalog.pending.$user")
    }
}

class StorageFailure : Exception("Не удалось прочитать или сохранить защищённые данные приложения.")

interface SessionStorage {
    fun load(): TokenPair?
    fun save(tokens: TokenPair)
    fun clear()
    fun isExpired(): Boolean
    fun markExpired()
    fun clearExpired()
}

class TokenVault(private val files: PrivateRecords) : SessionStorage {
    override fun load(): TokenPair? = files.read("session")?.let { value ->
        try { API_JSON.decodeFromString<TokenPair>(value) } catch (_: Exception) { throw StorageFailure() }
    }
    override fun save(tokens: TokenPair) {
        // Migrate the initial boolean latch before replacing its pair. A process death after the
        // atomic pair write then cannot let an old expiry marker invalidate recovered credentials.
        if (files.read("session-expired") == "true") {
            val previous = runCatching { load() }.getOrNull()
            files.write("session-expired", previous?.let { expiryMarker(it) } ?: "access.unreadable")
        }
        files.write("session", API_JSON.encodeToString(tokens))
    }
    override fun clear() { files.delete("session"); files.delete("session-expired") }
    override fun isExpired(): Boolean {
        val marker = files.read("session-expired") ?: return false
        val saved = load() ?: return true
        if (marker == "true") {
            files.write("session-expired", expiryMarker(saved))
            return true
        }
        return marker == expiryMarker(saved)
    }
    override fun markExpired() = files.write("session-expired", load()?.let { expiryMarker(it) } ?: "true")
    override fun clearExpired() = files.delete("session-expired")
    private fun expiryMarker(tokens: TokenPair) = "access." + storageHash(tokens.accessToken)
}
