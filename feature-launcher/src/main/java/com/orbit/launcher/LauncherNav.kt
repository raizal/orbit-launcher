package com.orbit.launcher

import android.content.Intent
import com.orbit.launcher.data.LauncherPrefs
import com.orbit.launcher.face.FaceData
import com.orbit.launcher.face.FaceRepo
import kotlinx.coroutines.flow.StateFlow

/** Launcher screens. FACE is home; the rest are one gesture or button away (docs/launcher.md). */
enum class Screen { FACE, APPS, APP_LIST, QUICK, TILES, NOTIFS, PICKER, CUSTOMIZE }

/** What every launcher screen can do. Screens take this instead of touching the activity. */
interface LauncherNav {
    /** BACK semantics: closes the current screen (to its parent or the face). */
    fun back()
    fun go(screen: Screen)

    /** Slide in the audio diary recorder (idle). */
    fun openDiary()
    /** Start a voice note now (one press) and show the recorder. */
    fun recordNow()
    fun openInbox()

    /** Start an app / settings activity with the launcher's transition. */
    fun launch(intent: Intent)

    /** Face being customized (set by the picker before going to CUSTOMIZE). */
    var editingFaceId: String?
}

/** Shared launcher state for screens that need faces / settings / live values. */
class LauncherEnv(
    val faces: FaceRepo,
    val prefs: LauncherPrefs,
    val data: StateFlow<FaceData>,
)
