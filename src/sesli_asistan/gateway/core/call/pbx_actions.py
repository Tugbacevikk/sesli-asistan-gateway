"""Çağrı oturumunun santral yanı: aktarma, operatör, kapatma eylemleri, aktarma sonucu, zarif bozulmada operatör."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from typing import TYPE_CHECKING, Any

from ..models import Action, CallEvent, DialogTurn, HangupAction, OperatorAction, TransferAction
from ..ports import PbxError
from .models import (
    CallState,
)

if TYPE_CHECKING:
    from .session import CallSession

log = logging.getLogger("sesli_asistan.gateway.core.call")   # adı değişmez (log süzgeçleri)


def _action_id() -> str:
    return f"a-{secrets.token_hex(4)}"


def _describe(a: Action) -> str:
    if isinstance(a, TransferAction):
        return f"transfer:{a.extension}"
    return f"{a.type}:{a.reason}"


class PbxActionsMixin:
    # oturumun bu dosyada değişen alanları (değerleri CallSession.__init__ verir; tipler mypy için)
    _transfer_deadline: float | None
    _speech_meta: dict[str, Any]

    async def _apply(self: CallSession, turn: DialogTurn, completed: bool) -> None:
        if self.state in (CallState.HANDED_OFF, CallState.ENDED):
            return
        op = next((a for a in turn.actions if isinstance(a, OperatorAction)), None)
        tr = next((a for a in turn.actions if isinstance(a, TransferAction)), None)
        hu = next((a for a in turn.actions if isinstance(a, HangupAction)), None)
        if hu is not None:
            await self._send_hangup(hu.reason)
        elif op is not None:
            await self._send_operator(op.reason)
        elif tr is not None and completed:
            await self._send_transfer(tr)
        else:
            if tr is not None:
                log.info("[%s] aktarma cümlesine söze girildi: %s'e aktarma iptal", self.call_id, tr.extension)
                self.dialog.state.pending_transfer = None
            if turn.ended:
                self._handed_off("transferred", "aktarma_tamam")
            else:
                self._listen()

    async def _send_transfer(self: CallSession, tr: TransferAction) -> None:
        self.state = CallState.TRANSFERRING
        self._emit(CallEvent.ACTION, action="transfer", extension=tr.extension)
        self._transfer_deadline = self.clock() + self.cfg.transfer_result_timeout_s
        try:
            await self.pbx.send_action(self.call_id, {"action_id": _action_id(), "type": "transfer",
                                                      "extension": tr.extension})
        except PbxError as e:
            log.error("[%s] santral aktarma isteğini almadı (%s)", self.call_id, e)
            await self._transfer_result("failed")

    async def _send_operator(self: CallSession, reason: str) -> None:
        self._emit(CallEvent.ACTION, action="operator", reason=reason)
        self._handed_off("operator", reason)
        try:
            await self.pbx.send_action(self.call_id, {"action_id": _action_id(), "type": "operator"})
        except PbxError as e:
            log.error("[%s] santral operatör isteğini almadı (%s): çağrı kapatılıyor", self.call_id, e)
            self.stats.outcome, self.stats.reason = "error", "santral_eylem_hatasi"
            self._stop.set()

    async def _send_hangup(self: CallSession, reason: str) -> None:
        self._emit(CallEvent.ACTION, action="hangup", reason=reason)
        self._handed_off("closed", reason)
        try:
            await self.pbx.send_action(self.call_id, {"action_id": _action_id(), "type": "hangup"})
        except PbxError as e:
            log.error("[%s] santral kapatma isteğini almadı (%s)", self.call_id, e)
            self._stop.set()

    async def _transfer_result(self: CallSession, status: str) -> None:
        if self.state != CallState.TRANSFERRING:
            log.warning("[%s] beklenmeyen aktarma sonucu (%s, durum %s)", self.call_id, status, self.state)
            return
        self._transfer_deadline = None
        turn = await self._thinking(self.dialog.handle_transfer_result(status))
        if status == "answered":
            self._handed_off("transferred", "cevaplandi")
            return
        self.state = CallState.THINKING
        self._start_speech(turn, heard_at=None, heard=f"[santral: {status}]")

    def _handed_off(self: CallSession, outcome: str, reason: str) -> None:
        self.state = CallState.HANDED_OFF
        self.stats.outcome, self.stats.reason = outcome, reason
        self._inputs.clear()

    def _operator_turn(self: CallSession, reason: str) -> DialogTurn:
        return DialogTurn(self.dialog.agent.cfg.texts.to_operator, [OperatorAction(reason)], fallback=True)

    async def _emergency_operator(self: CallSession, reason: str) -> None:
        """Hata anonsu (hazır ses; TTS çökmüş olabilir) + operatör."""
        if self._speaking():
            self._stop_speech()
        self._speech_meta = {}
        text = self.dialog.agent.cfg.texts.error
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.speaker.say(text, self.media), timeout=15)
        await self._send_operator(reason)
