"""Small adapter for sending conversational replies to WhatsApp."""


class WhatsAppReplySender:
    def __init__(self, wa_client):
        self.wa_client = wa_client

    async def send(self, chat_id: str, text: str) -> dict:
        return await self.wa_client.send_text(chat_id, text)


async def send(wa_client, chat_id: str, text: str) -> dict:
    return await WhatsAppReplySender(wa_client).send(chat_id, text)
