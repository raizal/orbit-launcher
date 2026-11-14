package com.orbit

import android.app.Application
import com.orbit.voice.upload.Uploads
import java.io.File

class OrbitApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // Application.onCreate also runs in the `:voice` recording process; keep that one lean.
        if (processName() == packageName) Uploads.init(this)
    }

    private fun processName() = File("/proc/self/cmdline").readText().trim('\u0000', ' ', '\n')
}
