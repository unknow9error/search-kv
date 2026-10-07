package kz.unknown.meken.core

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction
import java.util.UUID
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString

sealed interface ServerEvent {
    data class Accepted(val turnId: String) : ServerEvent
    data class Status(val text: String) : ServerEvent
    data class Notice(val text: String) : ServerEvent
    data class PreferencesChanged(val preferences: Preferences) : ServerEvent
    data class Listings(val items: List<Apartment>) : ServerEvent
    data class Message(val text: String, val citations: List<Citation>) : ServerEvent
    data class Suggestions(val items: List<String>) : ServerEvent
    data class Done(val status: String) : ServerEvent
    data object Pending : ServerEvent
    data class Failure(val text: String) : ServerEvent
    data object Ignored : ServerEvent
}

@Serializable private data class TextPayload(val text: String)
@Serializable private data class AcceptedPayload(@SerialName("turn_id") val turnId: String)
@Serializable private data class MessagePayload(val text: String, val citations: List<Citation>)
@Serializable private data class DonePayload(val status: String)

/** Unknown event kinds may be added by the backend without breaking older clients. */
fun decodeEvent(kind: String, data: String): ServerEvent = when (kind) {
    "accepted" -> ServerEvent.Accepted(API_JSON.decodeFromString<AcceptedPayload>(data).turnId)
    "status" -> ServerEvent.Status(API_JSON.decodeFromString<TextPayload>(data).text)
    "notice" -> ServerEvent.Notice(API_JSON.decodeFromString<TextPayload>(data).text)
    "preferences" -> ServerEvent.PreferencesChanged(API_JSON.decodeFromString<Preferences>(data))
    "listings" -> ServerEvent.Listings(API_JSON.decodeFromString<Items<Apartment>>(data).items)
    "message" -> API_JSON.decodeFromString<MessagePayload>(data).let { ServerEvent.Message(it.text, it.citations) }
    "suggestions" -> ServerEvent.Suggestions(API_JSON.decodeFromString<Items<String>>(data).items)
    "done" -> ServerEvent.Done(API_JSON.decodeFromString<DonePayload>(data).status)
    "pending" -> ServerEvent.Pending
    "error" -> ServerEvent.Failure(API_JSON.decodeFromString<TextPayload>(data).text)
    else -> ServerEvent.Ignored
}

data class SseFrame(val sequence: Int?, val kind: String, val data: String)

class SseProtocolException(message: String, cause: Throwable? = null) : IllegalArgumentException(message, cause)

/** Raw-byte parser: network chunk boundaries and split UTF-8 codepoints never affect framing. */
class SseByteParser(private val maxBytes: Int = 2_000_000) {
    init { require(maxBytes in 1..2_000_000) }

    private val line = ByteArrayOutputStream()
    private var previousCr = false
    private var firstLine = true
    private var eventBytes = 0
    private var kind = "message"
    private var sequence: Int? = null
    private val data = mutableListOf<String>()

    fun feed(byte: Int): SseFrame? {
        if (byte !in 0..255) throw SseProtocolException("Invalid byte")
        if (byte == 10 && previousCr) {
            previousCr = false
            return null
        }
        previousCr = byte == 13
        if (byte != 10 && byte != 13) {
            if (line.size() >= maxBytes) throw SseProtocolException("SSE line exceeds $maxBytes bytes")
            line.write(byte)
            return null
        }
        val bytes = line.toByteArray()
        line.reset()
        val decoder = Charsets.UTF_8.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
        var text = try { decoder.decode(ByteBuffer.wrap(bytes)).toString() }
        catch (error: CharacterCodingException) { throw SseProtocolException("Invalid SSE UTF-8", error) }
        if (firstLine) {
            firstLine = false
            text = text.removePrefix("\uFEFF")
        }
        if (text.isEmpty()) {
            val frame = if (data.isEmpty()) null else SseFrame(sequence, kind.ifEmpty { "message" }, data.joinToString("\n"))
            kind = "message"
            sequence = null
            data.clear()
            eventBytes = 0
            return frame
        }
        // Include ignored/comment lines in the bound, so they cannot grow an event indefinitely.
        eventBytes += bytes.size + 1
        if (eventBytes > maxBytes) throw SseProtocolException("SSE event exceeds $maxBytes bytes")
        if (text.startsWith(':')) return null
        val colon = text.indexOf(':')
        val field = if (colon < 0) text else text.substring(0, colon)
        var value = if (colon < 0) "" else text.substring(colon + 1)
        if (value.startsWith(' ')) value = value.substring(1)
        when (field) {
            "event" -> kind = value
            "id" -> if (!value.contains('\u0000')) sequence = value.toIntOrNull()
            "data" -> data.add(value)
        }
        return null
    }
}

data class ChatMessage(
    val id: String = UUID.randomUUID().toString(),
    val isUser: Boolean,
    val text: String,
    val citations: List<Citation> = emptyList(),
)

data class SearchState(
    val preferences: Preferences = Preferences(),
    val apartments: List<Apartment> = emptyList(),
    val messages: List<ChatMessage> = emptyList(),
    val suggestions: List<String> = emptyList(),
    val status: String = "",
    val notice: String? = null,
    val error: String? = null,
    val isStreaming: Boolean = false,
    val turnId: String? = null,
    val isPending: Boolean = false,
) {
    /** Sequence replay filtering belongs to the repository, before applying an event. */
    fun apply(event: ServerEvent): SearchState = when (event) {
        is ServerEvent.Accepted -> copy(turnId = event.turnId)
        is ServerEvent.Status -> copy(status = event.text)
        is ServerEvent.Notice -> copy(notice = event.text)
        is ServerEvent.PreferencesChanged -> copy(preferences = event.preferences)
        is ServerEvent.Listings -> copy(apartments = event.items.filter { it.status == "available" }.distinctBy { it.id })
        is ServerEvent.Message -> copy(messages = messages + ChatMessage(isUser = false, text = event.text, citations = event.citations))
        is ServerEvent.Suggestions -> copy(suggestions = event.items)
        is ServerEvent.Failure -> copy(error = event.text)
        is ServerEvent.Done -> copy(
            isStreaming = false,
            status = "",
            isPending = false,
            error = error ?: if (event.status == "complete") null else "Подбор прервался. Попробуйте ещё раз.",
        )
        ServerEvent.Pending -> copy(isPending = true)
        ServerEvent.Ignored -> this
    }
}
