package com.orbit.voice.inbox

import com.orbit.voice.NoteMeta
import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Talk sidecar's `about` (docs/inbox.md): present only on the turn that is about an item. */
class SidecarAboutTest {
    private val json = Json { prettyPrint = true; ignoreUnknownKeys = true } // as NoteStore writes it
    private fun meta(about: String?) = NoteMeta("id", "2026-11-15T10:00:00+07:00", 1000, "ab", "H10", "0.1.0", mode = "talk", about = about)

    @Test fun aboutIsWrittenWhenSet() {
        val text = json.encodeToString(NoteMeta.serializer(), meta("in-3f9c0a1b2d"))
        assertTrue(text.contains("\"about\": \"in-3f9c0a1b2d\""))
        assertEquals("in-3f9c0a1b2d", json.decodeFromString(NoteMeta.serializer(), text).about)
    }

    @Test fun absentOtherwise() {
        assertFalse(json.encodeToString(NoteMeta.serializer(), meta(null)).contains("about"))
    }
}
