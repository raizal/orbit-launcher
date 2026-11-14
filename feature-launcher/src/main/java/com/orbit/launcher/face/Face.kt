package com.orbit.launcher.face

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/**
 * Watch face file (`*.face.json`), see docs/launcher.md → Watch face format.
 * One permissive layer type: each `type` reads the fields it needs and ignores
 * the rest, so a face written for a newer launcher still loads.
 */
@Serializable
data class Face(
    val id: String,
    val name: String,
    val size: Float = 400f,
    val theme: Map<String, String> = emptyMap(),
    val layers: List<Layer> = emptyList(),
    val tweaks: Map<String, List<String>> = emptyMap(),
)

@Serializable
data class Layer(
    val type: String,
    // position / geometry (400-unit grid)
    val x: Float = 200f,
    val y: Float = 200f,
    val r: Float = 0f,
    val len: Float = 5f,
    val width: Float = 2f,
    val start: Float = 0f,
    val sweep: Float = 360f,
    // ticks
    val count: Int = 60,
    val major: Int = 0,
    val majorLen: Float? = null,
    val majorWidth: Float? = null,
    val majorColor: String? = null,
    // colors: theme key ("ink", "muted", "accent", …) or #RRGGBB
    val color: String = "ink",
    val track: String? = null,
    val fill: String? = null,
    // values / bindings
    val value: String? = null,
    val text: String = "",
    val size: Float = 16f,
    val weight: Int = 400,
    val font: String = "inter",
    val spacing: Float = 0f, // letter spacing, em
    val upper: Boolean = false,
    // hands
    val hand: String = "minute", // hour | minute | second
    val tail: Float = 0f,
    // complications
    val slot: Int = 0,
    val default: String = "none",
    val style: String = "bubble", // bubble | bare
    // group
    val layers: List<Layer> = emptyList(),
)

object FaceJson {
    private val json = Json { ignoreUnknownKeys = true; isLenient = true }

    fun parse(text: String): Face {
        val face = json.decodeFromString(Face.serializer(), text)
        require(face.id.isNotBlank()) { "face id is empty" }
        require(face.size > 0) { "size must be > 0" }
        return face
    }
}
