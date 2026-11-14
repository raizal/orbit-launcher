package com.orbit.voice

import android.content.Context
import android.media.MediaMetadataRetriever
import android.os.Build
import android.util.Log
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.UUID

/** Sidecar written next to each note; the companion trusts this, not file times. */
@Serializable
data class NoteMeta(
    val id: String,
    val started_at: String,
    val duration_ms: Long,
    val sha256: String,
    val device: String,
    val app_version: String,
    val recovered: Boolean = false,
)

/**
 * Files in `Android/data/com.orbit/files/notes/`:
 *  - `<stamp>.aac.rec` while recording (ADTS stays playable if we die),
 *  - `<stamp>.aac` + `<stamp>.json` once finalized.
 * Nothing here ever deletes audio.
 */
class NoteStore(private val context: Context) {

    val dir: File = File(context.getExternalFilesDir(null), "notes").apply { mkdirs() }

    /** `2026-11-13_12-08-42+0700` — local time with offset, filesystem-safe, sorts by name. */
    fun stampFor(epochMs: Long): String =
        SimpleDateFormat("yyyy-MM-dd_HH-mm-ssZ", Locale.US).format(Date(epochMs))

    fun tempFile(stamp: String) = File(dir, "$stamp$AUDIO_EXT$TEMP_SUFFIX")

    /** Renames the temp file and writes the sidecar. Returns the final audio file. */
    fun finalize(temp: File, startedAtMs: Long, durationMs: Long, recovered: Boolean = false): File {
        val audio = File(dir, temp.name.removeSuffix(TEMP_SUFFIX))
        if (!temp.renameTo(audio)) error("rename failed: ${temp.name}")
        val meta = NoteMeta(
            id = UUID.randomUUID().toString(),
            started_at = isoOffset(startedAtMs),
            duration_ms = durationMs,
            sha256 = sha256(audio),
            device = Build.MODEL,
            app_version = appVersion(),
            recovered = recovered,
        )
        // Write via a temp file so a crash never leaves half a sidecar.
        val json = File(dir, audio.nameWithoutExtension + ".json")
        val tmpJson = File(dir, json.name + ".tmp")
        tmpJson.writeText(JSON.encodeToString(NoteMeta.serializer(), meta))
        tmpJson.renameTo(json)
        return audio
    }

    /**
     * Finalizes temp files left by a crash or power loss. The service calls it
     * with `minIdleMs = 0` (it knows nothing is recording). The UI passes a
     * margin so a recording that is just starting in `:voice` is never touched.
     */
    fun recoverOrphans(minIdleMs: Long = 0) {
        val now = System.currentTimeMillis()
        dir.listFiles { f -> f.name.endsWith(TEMP_SUFFIX) && now - f.lastModified() >= minIdleMs }?.forEach { temp ->
            if (temp.length() == 0L) {
                Log.w(TAG, "empty orphan kept for inspection: ${temp.name}")
                return@forEach
            }
            runCatching {
                val startedAt = parseStamp(temp.name.removeSuffix(TEMP_SUFFIX).removeSuffix(AUDIO_EXT))
                finalize(temp, startedAt, durationOf(temp), recovered = true)
            }.onSuccess { Log.i(TAG, "recovered ${it.name}") }
                .onFailure { Log.e(TAG, "could not recover ${temp.name}", it) }
        }
        // A sidecar missing for a finished audio file (crash between rename and write).
        dir.listFiles { f -> f.name.endsWith(AUDIO_EXT) && now - f.lastModified() >= minIdleMs }?.forEach { audio ->
            if (!File(dir, audio.nameWithoutExtension + ".json").exists()) {
                val tmp = File(dir, audio.name + TEMP_SUFFIX)
                if (audio.renameTo(tmp)) {
                    runCatching { finalize(tmp, parseStamp(audio.nameWithoutExtension), durationOf(tmp), recovered = true) }
                        .onFailure { tmp.renameTo(audio); Log.e(TAG, "sidecar repair failed ${audio.name}", it) }
                }
            }
        }
    }

    fun meta(stem: String): NoteMeta? =
        runCatching { JSON.decodeFromString(NoteMeta.serializer(), File(dir, "$stem.json").readText()) }.getOrNull()

    /** Unfinished temp files (counted as waiting by the UI). */
    fun tempCount(): Int = dir.listFiles { f -> f.name.endsWith(TEMP_SUFFIX) }?.size ?: 0

    private fun durationOf(file: File): Long =
        runCatching {
            MediaMetadataRetriever().run {
                try {
                    setDataSource(file.absolutePath)
                    extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)?.toLong()
                } finally {
                    release()
                }
            }
        }.getOrNull()?.takeIf { it > 0 }
            ?: (file.length() * 8 * 1000 / BITRATE) // estimate from the fixed bitrate

    private fun parseStamp(stamp: String): Long =
        SimpleDateFormat("yyyy-MM-dd_HH-mm-ssZ", Locale.US).parse(stamp)!!.time

    private fun isoOffset(epochMs: Long): String =
        SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US).format(Date(epochMs))

    private fun sha256(file: File): String {
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

    private fun appVersion(): String =
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"

    companion object {
        const val AUDIO_EXT = ".aac"
        const val TEMP_SUFFIX = ".rec"
        const val BITRATE = 32_000
        private const val TAG = "OrbitVoice"
        private val JSON = Json { prettyPrint = true; ignoreUnknownKeys = true }
    }
}
