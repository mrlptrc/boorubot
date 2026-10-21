#!/usr/bin/env bash
# Refaz a demo do README (docs/demo/): banco inventado, painel sem worker e sem rede, prints e GIF.
# Uso, na raiz do projeto: scripts/demo/gerar.sh   (precisa de ffmpeg e ImageMagick)
set -euo pipefail
cd "$(dirname "$0")/../.."
tmp=$(mktemp -d); port=8799; out=docs/demo
trap 'kill $server 2>/dev/null || true; rm -rf "$tmp"' EXIT

uv run python -I scripts/demo/seed.py "$tmp/data" >/dev/null
uv run python -I scripts/demo/server.py "$tmp/data" $port & server=$!
until curl -sf -o /dev/null http://127.0.0.1:$port/; do sleep 0.5; done

# prints antes do vídeo: a gravação aprova uma arte
uv run python -I scripts/demo/shots.py http://127.0.0.1:$port "$tmp/raw" inicio=/ editor=/posts/10 artes=/artes \
  calendario=/calendario metricas=/metricas sites=/sites \
  celular-inicio=/@celular celular-editor=/posts/10@celular celular-calendario=/calendario@celular
mkdir -p $out
for n in inicio editor artes calendario metricas sites; do
  magick "$tmp/raw/$n.png" -resize 1440x -strip -colors 256 PNG8:$out/$n.png
done
for n in celular-inicio celular-editor celular-calendario; do
  magick "$tmp/raw/$n.png" -resize 585x -strip -colors 256 PNG8:$out/$n.png
done

video=$(uv run python -I scripts/demo/record.py http://127.0.0.1:$port "$tmp/video")
ffmpeg -v error -y -ss 0.6 -i "$video" -vf "fps=10,scale=960:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" $out/fluxo.gif
echo "pronto: $out"
