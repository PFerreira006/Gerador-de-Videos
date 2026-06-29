import os
import gc
import json
import urllib.request
import asyncio
import edge_tts
import re
import time
import subprocess
import requests

# ========================================================
PEXELS_API_KEY = "Qi4pJF0nlGcr0vQTpWCADIlJT9IDhtBgUFOAihTwPzoH0OYzyVEgCAZA"
# ========================================================

os.environ["IMAGEMAGICK_BINARY"] = r"C:\Program Files\ImageMagick-7.1.1-Q16-HDRI\magick.exe"

from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips

VERSAO = "v6.1"
print(f"📦 gerar_video.py {VERSAO}")


# ──────────────────────────────────────────────
# KEYWORD MAP — evita que o Ollama dê respostas erradas
# para temas comuns
# ──────────────────────────────────────────────

KEYWORD_MAP = {
    # tecnologia / IA
    "ia": "artificial intelligence", "inteligencia artificial": "artificial intelligence",
    "inteligência artificial": "artificial intelligence", "robot": "robot technology",
    "computador": "computer technology", "programacao": "coding programming",
    "programação": "coding programming", "software": "software technology",
    "internet": "internet technology", "redes sociais": "social media",
    # história
    "historia": "ancient history", "história": "ancient history",
    "guerra": "war battle", "medieval": "medieval castle", "castelo": "medieval castle",
    "roma": "ancient rome", "egipto": "ancient egypt",
    # ciência / espaço
    "espaco": "outer space", "espaço": "outer space", "planeta": "planets space",
    "universo": "universe galaxy", "astronomia": "astronomy stars",
    "ciencia": "science laboratory", "ciência": "science laboratory",
    # natureza
    "natureza": "nature landscape", "oceano": "ocean underwater",
    "mar": "ocean waves", "floresta": "forest trees",
    # animais — keywords específicas para melhor match no Pexels
    "cao": "dog pet", "cão": "dog pet", "gato": "cat pet",
    "animais": "animals wildlife", "cachorro": "puppy dog",
    "cavalo": "horse riding", "peixe": "fish underwater",
    "passaro": "birds flying", "pássaro": "birds flying",
    # desporto
    "futebol": "soccer football", "desporto": "sport athletic",
    "fitness": "fitness workout", "gym": "gym workout", "corrida": "running athlete",
    # finanças
    "financas": "finance money", "finanças": "finance money",
    "dinheiro": "money cash", "investimento": "investment stock market",
    "cripto": "cryptocurrency bitcoin", "bitcoin": "cryptocurrency bitcoin",
    # saúde
    "saude": "health medical", "saúde": "health medical",
    "meditacao": "meditation mindfulness", "meditação": "meditation mindfulness",
    # humor
    "humor": "funny comedy", "comedia": "comedy funny", "comédia": "comedy funny",
    # curiosidades / recordes
    "curiosidade": "amazing facts", "record": "world record", "récord": "world record",
    "mais velho": "old elderly", "mais rapido": "fast speed", "mais alto": "tall building",
    "mais pequeno": "tiny small", "mais grande": "giant large",
}

def obter_keyword(tema):
    """Tenta o mapa primeiro; só chama o Ollama se não encontrar."""
    tema_lower = tema.lower().strip()
    # Verificar correspondência parcial no mapa (mais específico primeiro)
    melhor = None
    melhor_len = 0
    for chave, kw in KEYWORD_MAP.items():
        if chave in tema_lower and len(chave) > melhor_len:
            melhor, melhor_len = kw, len(chave)
    if melhor:
        print(f"   -> Keyword do mapa: '{melhor}'")
        return melhor
    # Fallback para Ollama — pede 2-3 palavras em inglês
    resposta = _ollama(
        f"Para o tema '{tema}', dá 2 ou 3 palavras em inglês para pesquisar "
        f"um vídeo de fundo relevante no Pexels. "
        f"Exemplos: 'dog running', 'ancient egypt', 'space galaxy', 'fitness workout'. "
        f"Responde APENAS com as palavras, sem pontuação."
    )
    kw = re.sub(r'[^a-z ]', '', resposta.lower()).strip()
    kw = ' '.join(kw.split()[:3]) or "city"
    print(f"   -> Keyword do Ollama: '{kw}'")
    return kw


# ──────────────────────────────────────────────
# UTILIDADES
# ──────────────────────────────────────────────

def limpar_nome_arquivo(nome):
    nome = nome.lower()
    for src, dst in [('áàâãä','a'),('éèêë','e'),('íìîï','i'),('óòôõö','o'),('úùûü','u'),('ç','c')]:
        for c in src:
            nome = nome.replace(c, dst)
    nome = re.sub(r'[^a-z0-9_]', '_', nome)
    return nome[:20]


# ──────────────────────────────────────────────
# OLLAMA
# ──────────────────────────────────────────────

def _ollama(prompt):
    url  = "http://localhost:11434/api/generate"
    data = {"model": "llama3.2:3b", "prompt": prompt, "stream": False}
    req  = urllib.request.Request(url, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode()).get("response", "").strip()

def obter_guiao_ollama(tema):
    texto = _ollama(
        f"Escreve um guião MUITO CURTO (máximo 60 palavras, cerca de 20 segundos) "
        f"em português para um YouTube Short sobre: {tema}. "
        f"Começa com uma pergunta ou facto surpreendente. "
        f"Sem indicações de cena, sem parênteses, sem narrador, sem aspas. "
        f"Apenas texto corrido e direto."
    )
    # Remover aspas que o Ollama por vezes coloca à volta do texto
    texto = texto.strip().strip('"\' ')
    return texto


# ──────────────────────────────────────────────
# VOZ + TIMESTAMPS
# Correção do offset: mede o silêncio inicial do MP3
# e subtrai a todos os timestamps
# ──────────────────────────────────────────────

async def _stream_tts(texto, arquivo_audio, voice):
    communicate = edge_tts.Communicate(texto, voice)
    entries = []
    if os.path.exists(arquivo_audio):
        os.remove(arquivo_audio)
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            with open(arquivo_audio, "ab") as f:
                f.write(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            inicio  = chunk["offset"]  / 10_000_000
            duracao = chunk["duration"] / 10_000_000
            palavra = chunk.get("text", "").strip()
            if palavra:
                entries.append((inicio, inicio + duracao, palavra))
    return entries


def gerar_voz(texto, arquivo_audio):
    entries = asyncio.run(_stream_tts(texto, arquivo_audio, "pt-PT-DuarteNeural"))
    if not entries:
        print("   -> ⚠️  DuarteNeural sem WordBoundary — a tentar pt-BR-AntonioNeural...")
        entries = asyncio.run(_stream_tts(texto, arquivo_audio, "pt-BR-AntonioNeural"))

    if entries:
        # Os timestamps do Edge-TTS ja estao corretos relativamente ao MP3.
        # NAO subtrair o offset inicial: ele representa o silencio real antes
        # da primeira palavra, que coincide com o silencio no ficheiro de audio.
        print(f"   -> {len(entries)} palavras com timestamps (sem correcao de offset)")
    else:
        print("   -> Sem timestamps - modo uniforme")

    return entries


# ──────────────────────────────────────────────
# PEXELS
# ──────────────────────────────────────────────

def baixar_videos_fundo(keyword, nome_seguro, n=3):
    print(f"🔍 Pexels: '{keyword}' ({n} clips)...")
    headers = {"Authorization": PEXELS_API_KEY}

    videos = []
    for query in [keyword, "city", "nature"]:
        url = (f"https://api.pexels.com/v1/videos/search"
               f"?query={query}&orientation=portrait&per_page={n}&min_duration=5")
        videos = requests.get(url, headers=headers).json().get("videos", [])
        if videos:
            break

    ficheiros = []
    for i, video_data in enumerate(videos[:n]):
        vfiles   = video_data["video_files"]
        portrait = [f for f in vfiles if f.get("height", 0) > f.get("width", 0)]
        pool     = portrait if portrait else vfiles
        best     = sorted(pool, key=lambda f: abs(f.get("width", 0) - 1080))[0]

        caminho = f"fundo_{nome_seguro}_{i}.mp4"
        print(f"   ⬇️  Clip {i+1}/{n} ({best.get('width')}x{best.get('height')}) → {caminho}")
        with open(caminho, "wb") as f:
            f.write(requests.get(best["link"]).content)
        ficheiros.append(caminho)

    return ficheiros


# Lista global para rastrear clips de fundo abertos e fechá-los depois
_clips_fundo_abertos = []

def construir_fundo(ficheiros, duracao_total):
    global _clips_fundo_abertos
    _clips_fundo_abertos = []   # reset para cada vídeo

    clips = []
    for f in ficheiros:
        c = VideoFileClip(f)
        _clips_fundo_abertos.append(c)   # guardar referência para fechar depois
        dur_segura = max(c.duration - 0.15, 1.0)
        clips.append(c.subclipped(0, dur_segura))

    fundo = concatenate_videoclips(clips)
    if fundo.duration < duracao_total:
        reps  = int(duracao_total / fundo.duration) + 1
        fundo = concatenate_videoclips([fundo] * reps)

    return fundo.subclipped(0, duracao_total).resized((1080, 1920))


# ──────────────────────────────────────────────
# LEGENDAS  (via ffmpeg ASS subtitles — sem ImageMagick)
# ──────────────────────────────────────────────

def _agrupar(entries, max_palavras=4, padding=0.05):
    """
    Agrupa palavras em blocos de legenda por numero de palavras.
    Nao usa janela de tempo — apenas max_palavras por bloco.
    O padding estende cada bloco ate ao inicio do seguinte,
    eliminando piscagens entre legendas.
    """
    grupos, bloco = [], []
    t0 = t1 = None

    for inicio, fim, palavra in entries:
        if t0 is None:
            t0, t1 = inicio, fim

        if len(bloco) >= max_palavras:
            fim_bloco = min(inicio, t1 + padding)
            grupos.append((t0, fim_bloco, " ".join(bloco)))
            bloco = []
            t0 = inicio

        bloco.append(palavra)
        t1 = fim

    if bloco:
        grupos.append((t0, t1 + padding, " ".join(bloco)))

    return grupos


def _seg_para_ass(t):
    """Converte segundos para formato ASS: H:MM:SS.cc"""
    h  = int(t // 3600)
    m  = int((t % 3600) // 60)
    s  = int(t % 60)
    cs = int(round((t % 1) * 100))
    return f"{h}:{m:02}:{s:02}.{cs:02}"


def criar_legendas(entries, guiao, duracao_total):
    """Devolve lista de dicionarios com os blocos de legenda."""
    if entries:
        grupos = _agrupar(entries)
        print(f"   {len(grupos)} blocos sincronizados")
    else:
        palavras = guiao.split()
        lista    = [" ".join(palavras[i:i+4]) for i in range(0, len(palavras), 4)]
        dur      = duracao_total / len(lista) if lista else duracao_total
        grupos   = [(i*dur, (i+1)*dur, g) for i, g in enumerate(lista)]
        print(f"   {len(grupos)} blocos uniformes (sem timestamps)")

    blocos = []
    for inicio, fim, texto in grupos:
        fim_real = min(fim, duracao_total - 0.05)
        if inicio >= duracao_total:
            continue
        blocos.append({
            "inicio": round(inicio, 4),
            "fim":    round(max(fim_real, inicio + 0.5), 4),
            "texto":  texto,
        })
    return blocos


def _gerar_ass(blocos, arquivo_ass, fonte_nome="Arial Bold"):
    """
    Gera um ficheiro ASS com legendas centradas na horizontal e a 75% da altura.
    O formato ASS e nativo do ffmpeg e suporta centragem perfeita,
    quebra de linha automatica e estilo consistente sem bugs do drawtext.

    Alignment=2  -> centro-inferior (padrao de legendas)
    MarginV=480  -> 75% de 1920 a contar do fundo = margem de 480px do fundo
                    (1920 * 0.25 = 480)
    """
    cabecalho = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,480,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    linhas = [cabecalho]
    for b in blocos:
        t_ini = _seg_para_ass(b["inicio"])
        t_fim = _seg_para_ass(b["fim"])
        # \N e a quebra de linha do formato ASS
        texto_ass = b["texto"].replace("\n", r"\N")
        linhas.append(f"Dialogue: 0,{t_ini},{t_fim},Default,,0,0,0,,{texto_ass}\n")

    with open(arquivo_ass, "w", encoding="utf-8") as f:
        f.writelines(linhas)


def aplicar_legendas_ffmpeg(video_sem_leg, arquivo_final, blocos, fonte):
    """
    Aplica legendas usando ficheiro ASS via ffmpeg subtitles filter.
    Centradas, com outline, sem bugs de drawtext.
    """
    if not blocos:
        import shutil
        shutil.move(video_sem_leg, arquivo_final)
        return

    arquivo_ass = arquivo_final.replace(".mp4", ".ass")
    _gerar_ass(blocos, arquivo_ass)

    # O filtro subtitles do ffmpeg le o .ass e renderiza nativamente
    # force_style substitui o nome da fonte pelo caminho completo no Windows
    # Converter path para forward slashes (obrigatorio no filtro subtitles do ffmpeg)
    ass_path = arquivo_ass.replace("\\", "/").replace(":", "\\:")
    filtro = f"subtitles={ass_path}"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_sem_leg,
        "-vf", filtro,
        "-codec:a", "copy",
        "-codec:v", "libx264",
        "-crf", "18",
        "-preset", "fast",
        arquivo_final
    ]

    print("   A gravar legendas ASS com ffmpeg...")
    resultado = subprocess.run(cmd, capture_output=True, text=True)

    # Apagar ficheiro ASS temporario
    try:
        os.remove(arquivo_ass)
    except Exception:
        pass

    if resultado.returncode != 0:
        print(f"   ERRO ffmpeg:\n{resultado.stderr[-800:]}")
        raise RuntimeError("ffmpeg subtitles falhou")
    print(f"   Legendas gravadas: {arquivo_final}")


# ──────────────────────────────────────────────
# LIMPEZA ROBUSTA (resolve WinError 32)
# ──────────────────────────────────────────────

def fechar_e_limpar(clips_para_fechar, ficheiros_para_apagar):
    global _clips_fundo_abertos

    # 1. Fechar clips compostos (fundo, audio, final)
    todos_os_clips = clips_para_fechar + _clips_fundo_abertos
    for clip in todos_os_clips:
        try:
            if clip is not None:
                clip.close()
        except Exception:
            pass
    _clips_fundo_abertos = []

    # 2. Forçar garbage collection — liberta handles do ffmpeg/Windows
    gc.collect()
    time.sleep(3)

    # 3. Tentar apagar cada ficheiro até 8 vezes (Windows pode demorar a largar)
    for f in ficheiros_para_apagar:
        apagado = False
        for tentativa in range(8):
            try:
                if os.path.exists(f):
                    os.remove(f)
                print(f"   Apagado: {f}")
                apagado = True
                break
            except PermissionError:
                if tentativa < 7:
                    time.sleep(1.5)
            except Exception:
                break
        if not apagado and os.path.exists(f):
            print(f"   Nao foi possivel apagar '{f}' — podes apagar manualmente")


# ──────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def criar_video_completo(tema):
    nome_seguro    = limpar_nome_arquivo(tema)
    arquivo_audio  = os.path.join("audios", f"{nome_seguro}.mp3")
    arquivo_video  = os.path.join("videos_finais", f"{nome_seguro}.mp4")
    ficheiros_temp = []
    audio_clip = video_fundo = video_final = None

    try:
        # 1. Guião + keyword
        print(f"\n🧠 [1/4] Guião para '{tema}'...")
        guiao   = obter_guiao_ollama(tema)
        keyword = obter_keyword(tema)
        print(f"   -> {len(guiao.split())} palavras: {guiao[:80]}...")

        # 2. Voz
        print("🎙️  [2/4] Voz com timestamps...")
        entries = gerar_voz(guiao, arquivo_audio)

        # 3. Fundo
        print("🌐 [3/4] Clips de fundo...")
        ficheiros_temp = baixar_videos_fundo(keyword, nome_seguro, n=3)

        # 4. Montagem
        print("🎬 [4/4] A montar...")
        audio_clip    = AudioFileClip(arquivo_audio)
        duracao_total = audio_clip.duration
        print(f"   -> Áudio: {duracao_total:.1f}s")

        video_fundo = construir_fundo(ficheiros_temp, duracao_total)
        video_fundo = video_fundo.with_audio(audio_clip)

        # Guardar video sem legendas num ficheiro temporario
        video_temp = os.path.join("videos_finais", f"_tmp_{nome_seguro}.mp4")
        video_final = video_fundo
        video_final.write_videofile(
            video_temp, fps=24, codec="libx264",
            audio_codec="aac", threads=4, logger="bar"
        )

        # Aplicar legendas com ffmpeg ASS subtitles
        blocos = criar_legendas(entries, guiao, duracao_total)
        fonte  = r"C:/Windows/Fonts/arialbd.ttf"
        aplicar_legendas_ffmpeg(video_temp, arquivo_video, blocos, fonte)
        print(f"CONCLUIDO: {arquivo_video}\n" + "="*40)

    finally:
        fechar_e_limpar(
            clips_para_fechar=[audio_clip, video_fundo, video_final],
            ficheiros_para_apagar=ficheiros_temp + ([video_temp] if "video_temp" in locals() and os.path.exists(video_temp) else [])
        )


# ──────────────────────────────────────────────
# ENTRADA
# ──────────────────────────────────────────────

def main():
    print("🌟 FÁBRICA DE YOUTUBE SHORTS 🌟")
    os.makedirs("audios", exist_ok=True)
    os.makedirs("videos_finais", exist_ok=True)

    print("\n📝 Temas separados por vírgulas.")
    print("👉 Exemplo: Historia de portugal, IA, Curiosidades do espaco")
    entrada = input("\n❓ Temas: ")

    temas = [t.strip() for t in entrada.split(",") if t.strip()]
    print(f"\n🚀 A produzir {len(temas)} vídeo(s)...\n")

    for i, tema in enumerate(temas, 1):
        print(f"{'='*40}\n🎬 VÍDEO {i}/{len(temas)}: {tema}\n{'='*40}")
        try:
            criar_video_completo(tema)
        except Exception as e:
            print(f"❌ Erro em '{tema}': {e}")

    print("\n🏆 CONCLUÍDO! Pasta: videos_finais/")


if __name__ == "__main__":
    main()