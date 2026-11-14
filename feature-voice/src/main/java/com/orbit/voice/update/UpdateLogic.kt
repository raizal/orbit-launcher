package com.orbit.voice.update

import com.orbit.voice.NoteStore
import com.orbit.voice.talk.TalkUi
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.security.MessageDigest

/** `GET /v1/app/latest` (docs/voice-notes.md → App updates). */
@Serializable
data class UpdateInfo(
    val version_code: Long,
    val version_name: String,
    val sha256: String,
    val size: Long,
    val published_at: String = "",
    val apk: String = "/v1/app/apk",
)

/** Pure rules of the updater (no Android), unit-tested. */
object UpdateLogic {
    /** Opportunistic checks (Wi-Fi connect, after uploads) run at most this often. */
    const val MIN_CHECK_INTERVAL_MS = 30 * 60 * 1000L
    /** A `.rec` touched this recently is a recording in progress (the service writes it every few 100 ms). */
    const val REC_ACTIVE_MS = 60_000L

    private val JSON = Json { ignoreUnknownKeys = true }

    /** Null for anything that is not a sane release description. */
    fun parse(body: String): UpdateInfo? =
        runCatching { JSON.decodeFromString(UpdateInfo.serializer(), body) }.getOrNull()
            ?.takeIf { it.version_code > 0 && it.size > 0 && SHA.matches(it.sha256) && it.version_name.isNotBlank() }
            ?.let { it.copy(sha256 = it.sha256.lowercase(), apk = safePath(it.apk)) }

    fun encode(info: UpdateInfo): String = JSON.encodeToString(UpdateInfo.serializer(), info)

    fun isNewer(info: UpdateInfo?, installedCode: Long): Boolean = info != null && info.version_code > installedCode

    fun dueForCheck(lastCheckMs: Long, nowMs: Long): Boolean =
        nowMs - lastCheckMs >= MIN_CHECK_INTERVAL_MS || nowMs < lastCheckMs // clock went back

    /** Only a path on the paired companion (never another host). */
    fun safePath(p: String): String = p.takeIf { it.startsWith("/") && !it.startsWith("//") } ?: "/v1/app/apk"

    /** A recording is being written: a non-cancelled `.rec` modified within [REC_ACTIVE_MS]. */
    fun recordingActive(files: List<Pair<String, Long>>, nowMs: Long): Boolean = files.any { (name, modified) ->
        name.endsWith(NoteStore.TEMP_SUFFIX) && nowMs - modified < REC_ACTIVE_MS
    }

    /** Talk states an install must not cut off: recording, sending/waiting for the reply, speaking it. */
    fun talkBlocks(ui: TalkUi): Boolean = ui is TalkUi.Listening || ui is TalkUi.Waiting || ui is TalkUi.Speaking

    /** True if [file] is exactly the published APK. */
    fun verify(file: File, info: UpdateInfo): Boolean =
        file.isFile && file.length() == info.size && sha256(file).equals(info.sha256, ignoreCase = true)

    fun sha256(file: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(64 * 1024)
            while (true) {
                val n = input.read(buf)
                if (n < 0) break
                md.update(buf, 0, n)
            }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    private val SHA = Regex("^[0-9a-fA-F]{64}$")
}
