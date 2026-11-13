package com.orbit.core

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp

object OrbitColors {
    val Bg = Color(0xFF000000)
    val Ink = Color(0xFFF2F1EC)
    val Muted = Color(0xFF9A9A94)
    val Surface = Color(0xFF1C1C1A)
    val Accent = Color(0xFFFFB547)
    val Rec = Color(0xFFFF5A4E)
    val Info = Color(0xFF5CD6E0)
    val OnAccent = Color(0xFF141413)
    val Track = Color(0xFF2A2A27)
}

object OrbitFonts {
    val Inter = FontFamily(
        Font(R.font.inter_regular, FontWeight.Normal),
        Font(R.font.inter_medium, FontWeight.Medium),
        Font(R.font.inter_semibold, FontWeight.SemiBold),
    )
    val Grotesk = FontFamily(
        Font(R.font.grotesk_medium, FontWeight.Medium),
        Font(R.font.grotesk_semibold, FontWeight.SemiBold),
    )
}

/** Every screen in `design/` is drawn on a 400 × 400 grid. */
const val DESIGN_SIZE = 400

/**
 * Round watch screen. Overrides density so that 1 dp == 1 design px, whatever
 * density the vendor forces on apps (see docs/device-facts.md). Content is a
 * 400 × 400 dp box clipped to a circle; place children with the absolute
 * offsets from the design files.
 */
@Composable
fun RoundScreen(content: @Composable () -> Unit) {
    BoxWithConstraints(
        Modifier.fillMaxSize().background(OrbitColors.Bg),
        contentAlignment = Alignment.Center,
    ) {
        val sidePx = with(LocalDensity.current) { minOf(maxWidth, maxHeight).toPx() }
        CompositionLocalProvider(LocalDensity provides Density(sidePx / DESIGN_SIZE, fontScale = 1f)) {
            Box(Modifier.size(DESIGN_SIZE.dp).clip(CircleShape)) { content() }
        }
    }
}
