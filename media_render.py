#!/usr/bin/env python3

import logging
import sys
from contextlib import contextmanager

import Ice
from Ice import identityToString as id2str

from gst_player import GstPlayer

Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice  # type: ignore # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaRender")


class MediaRenderI(Spotifice.MediaRender):
    def __init__(self, player):
        self.player = player
        self.server: Spotifice.MediaServerPrx = None
        self.current_track = None
        self.playlist = None
        self.index = -1
        self.repeat = False
        self._paused = False
        self.secure = None

    def ensure_player_stopped(self):
        if self.player.is_playing():
            raise Spotifice.PlayerError(reason="Already playing")

    def ensure_server_bound(self):
        if not self.server:
            raise Spotifice.BadReference(reason="No MediaServer bound")

    # --- RenderConnectivity ---

    def bind_media_server(self, media_server,secure_stream_mgr, current=None):
        try:
            proxy = media_server.ice_timeout(500)
            proxy.ice_ping()
        except Ice.ConnectionRefusedException as e:
            raise Spotifice.BadReference(reason=f"MediaServer not reachable: {e}")

        self.server = media_server
        self.secure = secure_stream_mgr
        logger.info(f"Bound to MediaServer '{id2str(media_server.ice_getIdentity())}'")

    def unbind_media_server(self, current=None):
        self.server = None
        self.secure = None
        logger.info("Unbound MediaServer")

    # --- ContentManager ---

    def load_track(self, track_id, current=None):
        self.ensure_server_bound()

        try:
            with self.keep_playing_state(current):
                self.current_track = self.server.get_track_info(track_id)
                if self.playlist:
                    try:
                        self.index = self.playlist.track_ids.index(track_id)
                    except ValueError:
                        pass

            logger.info(f"Current track set to: {self.current_track.title}")

        except Spotifice.TrackError as e:
            logger.error(f"Error setting track: {e.reason}")
            raise

    def get_current_track(self, current=None):
        return self.current_track

    def load_playlist(self, playlist_id, current=None):
        self.ensure_server_bound()
        self.ensure_player_stopped()

        pl = self.server.get_playlist(playlist_id)
        for tid in pl.track_ids:
            self.server.get_track_info(tid)

        self.playlist = pl
        self.index = 0 if pl.track_ids else -1
        self.current_track = (
            self.server.get_track_info(pl.track_ids[0]) if self.index == 0 else None
        )
        self._paused = False
        logger.info(f"Playlist cargada: {pl.id} con {len(pl.track_ids)} pistas")

    # --- PlaybackController ---

    @contextmanager
    def keep_playing_state(self, current):
        playing = self.player.is_playing()
        if playing:
            self.stop(current)
        try:
            yield
        finally:
            if playing:
                self.play(current)

    def play(self, current=None):
        if not self.secure:
            raise Spotifice.BadReference(reason="Secure session not established")

        if self.player.is_playing():
            raise Spotifice.PlayerError(reason="Already playing")

        if not self.current_track:
            raise Spotifice.TrackError(reason="No track loaded")

        # Abrir stream seguro (v2)
        self.secure.open_stream(self.current_track.id)

        def get_chunk_hook(chunk_size):
            try:
                return self.secure.get_audio_chunk(chunk_size)
            except Exception as e:
                logger.error(f"Secure stream error: {e}")
                return b""

        self.player.configure(get_chunk_hook)

        if not self.player.confirm_play_starts():
            raise Spotifice.PlayerError(reason="Failed to confirm playback")

        self._paused = False


    def stop(self, current=None):
        if self.secure:
            try:
                self.secure.close_stream()
            except Exception:
                pass

        if not self.player.stop():
            raise Spotifice.PlayerError(reason="Failed to stop player")

        self._paused = False


    def pause(self, current=None):
        if not self.player.stop():
            raise Spotifice.PlayerError(reason="Pause failed")

        if self.secure:
            try:
                self.secure.close_stream()
            except Exception:
                pass

        self._paused = True


    def get_status(self, current=None):
        if self.player.is_playing():
            state = Spotifice.PlaybackState.PLAYING
        elif self._paused:
            state = Spotifice.PlaybackState.PAUSED
        else:
            state = Spotifice.PlaybackState.STOPPED
        tid = self.current_track.id if self.current_track else ""
        return Spotifice.PlaybackStatus(state=state, current_track_id=tid, repeat=self.repeat)

    def next(self, current=None):
        if not self.playlist or not self.playlist.track_ids:
            raise Spotifice.PlaylistError(reason="No playlist loaded")
        with self.keep_playing_state(current):
            if self.index < 0:
                self.index = 0
            else:
                self.index += 1
            if self.index >= len(self.playlist.track_ids):
                if self.repeat:
                    self.index = 0
                else:
                    self.index = len(self.playlist.track_ids) - 1
                    return
            tid = self.playlist.track_ids[self.index]
            self.current_track = self.server.get_track_info(tid)

    def previous(self, current=None):
        if not self.playlist or not self.playlist.track_ids:
            raise Spotifice.PlaylistError(reason="No playlist loaded")
        with self.keep_playing_state(current):
            if self.index < 0:
                self.index = 0
            else:
                self.index -= 1
            if self.index < 0:
                if self.repeat:
                    self.index = len(self.playlist.track_ids) - 1
                else:
                    self.index = 0
            tid = self.playlist.track_ids[self.index]
            self.current_track = self.server.get_track_info(tid)

    def set_repeat(self, value, current=None):
        self.repeat = bool(value)




def main(ic, player):
    servant = MediaRenderI(player)

    adapter = ic.createObjectAdapter("MediaRenderAdapter")
    proxy = adapter.add(servant, ic.stringToIdentity("mediaRender1"))
    logger.info(f"MediaRender: {proxy}")

    adapter.activate()
    ic.waitForShutdown()

    logger.info("Shutdown")


if __name__ == "__main__":
    player = GstPlayer()
    player.start()
    try:
        with Ice.initialize(sys.argv) as communicator:
            main(communicator, player)
    except KeyboardInterrupt:
        logger.info("Render interrupted by user.")
    finally:
        player.shutdown()