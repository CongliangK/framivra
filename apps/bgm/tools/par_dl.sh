#!/usr/bin/env bash
# 16 路并行分块下载：每块独立文件、按尺寸校验、失败整块重试（块 ~750MB 可接受）
PROXY="${HTTPS_PROXY:-}"
CURL_PROXY=()
[ -n "$PROXY" ] && CURL_PROXY=(--proxy "$PROXY")
REPO="$1"; REMOTE="$2"; OUT="$3"; N=16
URL="https://huggingface.co/$REPO/resolve/main/$REMOTE"
size=$(curl "${CURL_PROXY[@]}" -sIL --max-time 60 "$URL" | grep -i '^content-length' | tail -1 | tr -dc '0-9')
[ -z "$size" ] && { echo "FAIL: no content-length for $REMOTE"; exit 1; }
echo "$REMOTE: $((size/1048576))MB in $N chunks"
chunk=$(( size / N + 1 ))
tmp="${OUT}.part"; mkdir -p "$tmp"

dl_chunk() {
  local i=$1 s=$2 e=$3 want=$(( e - s + 1 )) try cur
  for try in 1 2 3 4 5 6 7 8 9 10; do
    cur=0; [ -f "$tmp/$i" ] && cur=$(stat -c%s "$tmp/$i")
    [ $cur -eq $want ] && return 0
    # 只补缺失的尾部区间，用追加拼接
    curl "${CURL_PROXY[@]}" -sL --max-time 2400 -o "$tmp/$i.tail" -r $((s+cur))-$e "$URL"
    [ -f "$tmp/$i.tail" ] && cat "$tmp/$i.tail" >> "$tmp/$i" && rm -f "$tmp/$i.tail"
  done
  cur=$(stat -c%s "$tmp/$i" 2>/dev/null || echo 0)
  [ $cur -eq $want ]
}

pids=()
for i in $(seq 0 $((N-1))); do
  s=$(( i * chunk )); [ $s -ge $size ] && break
  e=$(( s + chunk - 1 )); [ $e -ge $size ] && e=$(( size - 1 ))
  dl_chunk $i $s $e & pids+=($!)
done
rc=0
for p in "${pids[@]}"; do wait $p || rc=1; done
[ $rc -ne 0 ] && { echo "FAIL chunks incomplete: $REMOTE"; exit 1; }

> "$OUT"
for i in $(seq 0 $((N-1))); do [ -f "$tmp/$i" ] && cat "$tmp/$i" >> "$OUT"; done
got=$(stat -c%s "$OUT")
if [ "$got" -eq "$size" ]; then echo "DONE $REMOTE ($((size/1048576))MB)"; rm -rf "$tmp"
else echo "SIZE-MISMATCH $REMOTE got=$got want=$size"; exit 1; fi
