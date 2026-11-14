package com.orbit.launcher.face

import android.content.Context
import android.os.FileObserver
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import java.io.File

/** A face that loaded, or the file that failed and why. */
sealed interface FaceEntry {
    val id: String
    data class Ok(val face: Face, val source: String) : FaceEntry { override val id get() = face.id }
    data class Broken(override val id: String, val file: String, val error: String) : FaceEntry
}

/**
 * Faces bundled in assets/faces plus `Android/data/com.orbit/files/faces/<name>.face.json`
 * (push with adb; a file with the same id overrides the bundled one). Watches the
 * folder and reloads on change. A bad file becomes [FaceEntry.Broken], never a crash.
 */
class FaceRepo(private val context: Context) {
    val dir: File = File(context.getExternalFilesDir(null), "faces").apply { mkdirs() }
    private val _faces = MutableStateFlow(load())
    val faces: StateFlow<List<FaceEntry>> = _faces.asStateFlow()

    @Suppress("DEPRECATION") // FileObserver(File) is API 29+
    private val observer = object : FileObserver(dir.absolutePath, CLOSE_WRITE or MOVED_TO or DELETE or MOVED_FROM) {
        override fun onEvent(event: Int, path: String?) {
            if (path?.endsWith(SUFFIX) == true) _faces.value = load()
        }
    }

    private var fingerprint = fingerprint()

    fun start() = observer.startWatching()
    fun stop() = observer.stopWatching()

    /**
     * Reloads if the folder changed. FileObserver misses files pushed by adb on
     * Android 8.1 (sdcardfs), so the launcher also polls this every few seconds.
     */
    fun pollForChanges() {
        val now = fingerprint()
        if (now != fingerprint) {
            fingerprint = now
            _faces.value = load()
        }
    }

    private fun fingerprint(): String =
        dir.listFiles { f -> f.name.endsWith(SUFFIX) }.orEmpty().sortedBy { it.name }
            .joinToString("|") { "${it.name}:${it.length()}:${it.lastModified()}" }

    private fun load(): List<FaceEntry> {
        val bundled = context.assets.list("faces").orEmpty().filter { it.endsWith(SUFFIX) }.map { name ->
            parse(name, "asset") { context.assets.open("faces/$name").bufferedReader().readText() }
        }
        val pushed = dir.listFiles { f -> f.name.endsWith(SUFFIX) }.orEmpty().sortedBy { it.name }.map { f ->
            parse(f.name, "file") { f.readText() }
        }
        val byId = LinkedHashMap<String, FaceEntry>()
        (bundled + pushed).forEach { byId[it.id] = it }
        return byId.values.toList()
    }

    private fun parse(name: String, source: String, read: () -> String): FaceEntry =
        runCatching { FaceEntry.Ok(FaceJson.parse(read()), source) }.getOrElse {
            Log.w("OrbitFace", "bad face $name", it)
            FaceEntry.Broken(name.removeSuffix(SUFFIX), name, it.message?.take(120) ?: it.javaClass.simpleName)
        }

    companion object {
        const val SUFFIX = ".face.json"

        /** Shown instead of a broken face: time + the error, so the watch stays usable. */
        fun errorFace(entry: FaceEntry.Broken) = Face(
            id = entry.id, name = "Error",
            theme = mapOf("bg" to "#000000", "ink" to "#F2F1EC", "muted" to "#9A9A94", "accent" to "#FF5A4E"),
            layers = listOf(
                Layer(type = "text", text = "{time.HH}:{time.mm}", y = 170f, size = 84f, weight = 600, font = "grotesk"),
                Layer(type = "text", text = "Face error: ${entry.file}", y = 240f, size = 14f, color = "accent"),
                Layer(type = "text", text = entry.error.replace("{", "(").replace("}", ")").take(48), y = 264f, size = 11f, color = "muted"),
            ),
        )
    }
}
