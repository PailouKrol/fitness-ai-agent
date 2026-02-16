import aiohttp
from config import api_key, proxy_url


headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {api_key}"
}




async def call_openai(payload):
    timeout = aiohttp.ClientTimeout(total=120)  # ✅ УВЕЛИЧИВАЕМ ДО 120 СЕКУНД
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.openai.com/v1/chat/completions",
            proxy=proxy_url,
            json=payload,
            headers=headers
        ) as response:
            return await response.json()


async def voice_openai(
    file_bytes: bytes,
    filename: str = "audio.ogg",
    response_format: str = "text"
) -> str | dict:
    form = aiohttp.FormData()
    form.add_field("file", file_bytes, filename=filename, content_type="audio/ogg")
    form.add_field("model", "whisper-1")
    form.add_field("response_format", response_format)
    form.add_field("temperature", str(0.1))

    timeout = aiohttp.ClientTimeout(total=120)  # ✅ УВЕЛИЧИВАЕМ ДО 120 СЕКУНД
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            "https://api.openai.com/v1/audio/transcriptions",
            data=form,
            headers={"Authorization": f"Bearer {api_key}"},
            proxy=proxy_url
        ) as resp:
            if resp.status != 200:
                txt = await resp.text()
                raise RuntimeError(f"OpenAI ASR error {resp.status}: {txt}")
            return await (resp.text() if response_format == "text" else resp.json())


