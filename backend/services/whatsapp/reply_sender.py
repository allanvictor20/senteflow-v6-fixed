"""
SenteFlow AI — WhatsApp Reply Sender (services/whatsapp/ variant)
==================================================================
Re-exports the integrations/whatsapp/reply_sender module so callers in
services/ and workflows/ that import from `services.whatsapp.reply_sender`
get the same voice-aware behaviour.

See integrations/whatsapp/reply_sender.py for the actual implementation.
"""

from integrations.whatsapp.reply_sender import (  # noqa: F401
    WhatsAppReplySender,
    send,
    send_voice,
    send_voice_aware,
)
