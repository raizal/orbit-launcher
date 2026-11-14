package com.orbit.launcher.apps

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextStyle
import com.orbit.core.OrbitColors
import com.orbit.launcher.LauncherEnv
import com.orbit.launcher.LauncherNav

/** TODO: placeholder until implemented. */
@Composable
fun AppGridScreen(nav: LauncherNav, env: LauncherEnv) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        BasicText("AppGridScreen", style = TextStyle(color = OrbitColors.Muted))
    }
}
