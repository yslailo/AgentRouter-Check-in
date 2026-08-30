#!/bin/bash
# setup-proxy.sh - 代理订阅解析、节点自动选择与 sing-box 启动
# 环境变量:
#   NODE_LINK（可选）- 单个代理节点链接，或订阅地址(URL)
#   PROXY_CONFIG_URL（可选）- 代理订阅地址，NODE_LINK 为空时使用

export LC_ALL=C
set -e

export NODE_LINK=${NODE_LINK:-''}
export PROXY_CONFIG_URL=${PROXY_CONFIG_URL:-''}

# ---- 确定节点来源 ----
CONFIG_URL=""
NODE_LIST=""

if [ -n "$NODE_LINK" ]; then
  if [[ "$NODE_LINK" == http://* || "$NODE_LINK" == https://* ]]; then
    CONFIG_URL="$NODE_LINK"
  else
    NODE_LIST="$NODE_LINK"
  fi
fi

if [ -z "$NODE_LIST" ] && [ -z "$CONFIG_URL" ] && [ -n "$PROXY_CONFIG_URL" ]; then
  CONFIG_URL="$PROXY_CONFIG_URL"
fi

if [ -z "$NODE_LIST" ] && [ -z "$CONFIG_URL" ]; then
  echo "[INFO] 未配置代理节点（NODE_LINK / PROXY_CONFIG_URL），跳过代理设置"
  exit 0
fi

# ---- 获取原始配置内容 ----
if [ -n "$CONFIG_URL" ]; then
  command -v curl &>/dev/null && FETCH="curl -sL" || FETCH="wget -qO-"
  echo "[INFO] 从订阅地址获取代理配置: $CONFIG_URL"
  RAW=$($FETCH "$CONFIG_URL")
  if [ -z "$RAW" ]; then
    echo "[ERROR] 无法获取代理配置（$CONFIG_URL）"
    exit 1
  fi
else
  RAW="$NODE_LIST"
fi

# ---- 解码节点列表（支持明文与 base64 / base64url）----
if grep -q '://' <<< "$RAW"; then
  NODES="$RAW"
else
  echo "[INFO] 检测到 base64 编码，解码节点列表..."
  B64=$(echo "$RAW" | tr -d '[:space:]' | tr '_-' '/+')
  MOD=$(( ${#B64} % 4 ))
  if [ $MOD -eq 2 ]; then B64="${B64}=="; elif [ $MOD -eq 3 ]; then B64="${B64}="; fi
  DECODED=$(echo "$B64" | base64 -d 2>/dev/null || true)
  if [ -z "$DECODED" ] || ! grep -q '://' <<< "$DECODED"; then
    echo "[ERROR] 代理配置解码失败"
    exit 1
  fi
  NODES="$DECODED"
fi

# ---- 提取节点 host:port 用于日志显示（避免泄露 uuid/密钥）----
display_node() {
  local link="$1"
  local after="${link#*@}"
  after="${after%%\?*}"
  after="${after%%#*}"
  if [ -n "$after" ] && [[ "$after" == *:* ]]; then
    echo "${after}"
  else
    echo "$(echo "$link" | cut -d: -f1)://***"
  fi
}

# ---- 提取有效节点（过滤空行 / 注释 / 非法链接）----
mapfile -t NODE_ARRAY <<< "$(echo "$NODES" | sed 's/\r$//' | grep -E '^[A-Za-z0-9]+://' | sed '/^\s*$/d')"
if [ ${#NODE_ARRAY[@]} -eq 0 ]; then
  echo "[ERROR] 未解析到有效代理节点"
  exit 1
fi
echo "[INFO] 解析到 ${#NODE_ARRAY[@]} 个代理节点:"
for node in "${NODE_ARRAY[@]}"; do
  echo "  - $(display_node "$node")"
done

# ---- 检查依赖 ----
if ! command -v jq &> /dev/null; then
  echo "[INFO] 安装 jq..."
  sudo apt-get update -qq && sudo apt-get install -y jq > /dev/null
fi

# ---- 下载 sing-box ----
command -v curl &>/dev/null && COMMAND="curl -sLo" || command -v wget &>/dev/null && COMMAND="wget -qO" || { echo "[ERROR] 需要 curl 或 wget"; exit 1; }

echo "[INFO] 获取 sing-box 最新版本..."
latest_version=$(curl -s "https://api.github.com/repos/SagerNet/sing-box/releases" | jq -r '[.[] | select(.prerelease==false)][0].tag_name | sub("^v"; "")')
if [ -z "$latest_version" ]; then
  echo "[WARN] 无法获取最新版本，使用 v1.13.14"
  latest_version="1.13.14"
fi
echo "[INFO] 使用版本: v${latest_version}"

ARCH_RAW=$(uname -m)
case "${ARCH_RAW}" in
    'x86_64' | 'amd64')  ARCH='amd64' ;;
    'x86' | 'i686' | 'i386') ARCH='386' ;;
    'aarch64' | 'arm64') ARCH='arm64' ;;
    'armv7l')  ARCH='armv7' ;;
    's390x')   ARCH='s390x' ;;
    *) echo "[ERROR] 不支持的架构: ${ARCH_RAW}"; exit 1 ;;
esac

echo "[INFO] 下载 sing-box..."
$COMMAND sing-box.tar.gz "https://github.com/SagerNet/sing-box/releases/download/v${latest_version}/sing-box-${latest_version}-linux-${ARCH}.tar.gz"
tar -xzf sing-box.tar.gz
mv "sing-box-${latest_version}-linux-${ARCH}/sing-box" ./
rm -rf sing-box.tar.gz "sing-box-${latest_version}-linux-${ARCH}"
chmod +x sing-box

# ---- 解析单节点并生成 sing-box 配置（写入 sing-box-config.json）----
# 使用全局变量 NODE_LINK 作为输入
generate_singbox_config() {
  # 解析协议
  proto=$(echo "$NODE_LINK" | cut -d':' -f1)
  content="${NODE_LINK#*://}"
  content="${content%%#*}"

  # URL 解码函数
  url_decode() {
    local encoded="$1"
    printf '%b' "$(echo "$encoded" | sed 's/%/\\x/g')"
  }

  # 初始化变量
  outbound_type=""
  outbound_server=""
  outbound_port=""
  outbound_uuid=""
  outbound_flow=""
  outbound_transport_type="tcp"
  outbound_path="/"
  outbound_host=""
  outbound_security="none"
  outbound_sni=""
  outbound_fingerprint="chrome"
  outbound_reality_pbk=""
  outbound_reality_sid=""
  outbound_password=""
  outbound_up_mbps=100
  outbound_down_mbps=100
  outbound_obfs_password=""
  outbound_auth=""
  outbound_congestion="bbr"
  outbound_udp_over_stream="true"
  outbound_zerortt="false"
  outbound_username=""
  outbound_password2=""
  outbound_version="5"
  outbound_insecure="false"
  outbound_alpn=""

  case "$proto" in
    vless)
      uuid_host="${content}"
      uuid="${uuid_host%%@*}"
      rest="${uuid_host#*@}"
      if [[ "$rest" == *"?"* ]]; then
        host_port="${rest%%\?*}"
        query="${rest#*\?}"
      else
        host_port="$rest"
        query=""
      fi
      outbound_server="${host_port%:*}"
      outbound_port="${host_port#*:}"
      outbound_uuid="$uuid"
      outbound_type="vless"

      if [ -n "$query" ]; then
        flow=$(echo "$query" | grep -o 'flow=[^&]*' | cut -d= -f2 || true)
        [ -n "$flow" ] && outbound_flow="$flow"
        ttype=$(echo "$query" | grep -o 'type=[^&]*' | cut -d= -f2 || true)
        [ -n "$ttype" ] && outbound_transport_type="$ttype"
        path_raw=$(echo "$query" | grep -o 'path=[^&]*' | cut -d= -f2 || true)
        if [ -n "$path_raw" ]; then
          path_decoded=$(url_decode "$path_raw")
          outbound_path="${path_decoded%%\?*}"
        fi
        host=$(echo "$query" | grep -o 'host=[^&]*' | cut -d= -f2 || true)
        [ -n "$host" ] && outbound_host="$host"
        sec=$(echo "$query" | grep -o 'security=[^&]*' | cut -d= -f2 || true)
        [ -n "$sec" ] && outbound_security="$sec"
        sni=$(echo "$query" | grep -o 'sni=[^&]*' | cut -d= -f2 || true)
        [ -n "$sni" ] && outbound_sni="$sni"
        fp=$(echo "$query" | grep -o 'fp=[^&]*' | cut -d= -f2 || true)
        [ -n "$fp" ] && outbound_fingerprint="$fp"
        pbk=$(echo "$query" | grep -o 'pbk=[^&]*' | cut -d= -f2 || true)
        [ -n "$pbk" ] && outbound_reality_pbk="$pbk"
        sid=$(echo "$query" | grep -o 'sid=[^&]*' | cut -d= -f2 || true)
        [ -n "$sid" ] && outbound_reality_sid="$sid"
        ins=$(echo "$query" | grep -o 'insecure=[^&]*' | cut -d= -f2 || true)
        [ "$ins" = "1" ] || [ "$ins" = "true" ] && outbound_insecure="true"
        alins=$(echo "$query" | grep -o 'allowInsecure=[^&]*' | cut -d= -f2 || true)
        [ "$alins" = "1" ] || [ "$alins" = "true" ] && outbound_insecure="true"
      fi
      [ -z "$outbound_host" ] && outbound_host="$outbound_server"
      [ -z "$outbound_sni" ] && outbound_sni="$outbound_server"
      ;;

    vmess)
      b64="${content}"
      mod=$(( ${#b64} % 4 ))
      if [ $mod -eq 2 ]; then b64="${b64}=="; elif [ $mod -eq 3 ]; then b64="${b64}="; fi
      decoded=$(echo "$b64" | base64 -d 2>/dev/null || true)
      if [ -z "$decoded" ]; then
        echo "[ERROR] VMess 解码失败"
        return 1
      fi

      add=$(echo "$decoded" | jq -r '.add // ""')
      port=$(echo "$decoded" | jq -r '.port // 443')
      id=$(echo "$decoded" | jq -r '.id // ""')
      net=$(echo "$decoded" | jq -r '.net // "tcp"')
      tls=$(echo "$decoded" | jq -r '.tls // ""')
      sni=$(echo "$decoded" | jq -r '.sni // ""')
      host=$(echo "$decoded" | jq -r '.host // ""')
      path_raw=$(echo "$decoded" | jq -r '.path // "/"')
      path_decoded=$(url_decode "$path_raw")
      outbound_path="${path_decoded%%\?*}"
      fp=$(echo "$decoded" | jq -r '.fp // "chrome"')

      outbound_type="vmess"
      outbound_server="$add"
      outbound_port="$port"
      outbound_uuid="$id"
      outbound_transport_type="$net"
      outbound_host="${host:-$add}"
      outbound_sni="${sni:-$add}"
      outbound_fingerprint="$fp"
      outbound_security="$tls"
      ;;

    trojan)
      pass_rest="${content}"
      password="${pass_rest%%@*}"
      rest="${pass_rest#*@}"
      if [[ "$rest" == *"?"* ]]; then
        host_port="${rest%%\?*}"
        query="${rest#*\?}"
      else
        host_port="$rest"
        query=""
      fi
      outbound_server="${host_port%:*}"
      outbound_port="${host_port#*:}"
      outbound_password="$password"
      outbound_type="trojan"

      if [ -n "$query" ]; then
        ttype=$(echo "$query" | grep -o 'type=[^&]*' | cut -d= -f2 || true)
        [ -n "$ttype" ] && outbound_transport_type="$ttype"
        path_raw=$(echo "$query" | grep -o 'path=[^&]*' | cut -d= -f2 || true)
        if [ -n "$path_raw" ]; then
          path_decoded=$(url_decode "$path_raw")
          outbound_path="${path_decoded%%\?*}"
        fi
        host=$(echo "$query" | grep -o 'host=[^&]*' | cut -d= -f2 || true)
        [ -n "$host" ] && outbound_host="$host"
        sni=$(echo "$query" | grep -o 'sni=[^&]*' | cut -d= -f2 || true)
        [ -n "$sni" ] && outbound_sni="$sni"
        fp=$(echo "$query" | grep -o 'fp=[^&]*' | cut -d= -f2 || true)
        [ -n "$fp" ] && outbound_fingerprint="$fp"
        ins=$(echo "$query" | grep -o 'insecure=[^&]*' | cut -d= -f2 || true)
        [ "$ins" = "1" ] || [ "$ins" = "true" ] && outbound_insecure="true"
      fi
      [ -z "$outbound_host" ] && outbound_host="$outbound_server"
      [ -z "$outbound_sni" ] && outbound_sni="$outbound_server"
      ;;

    hysteria2|hy2)
      auth=""
      if [[ "$content" == *"@"* ]]; then
        auth="${content%%@*}"
        host_port="${content#*@}"
      else
        host_port="$content"
      fi
      if [[ "$host_port" == *"?"* ]]; then
        hp="${host_port%%\?*}"
        query="${host_port#*\?}"
      else
        hp="$host_port"
        query=""
      fi
      hp="${hp%/}"
      outbound_server="${hp%:*}"
      outbound_port="${hp#*:}"
      outbound_type="hysteria2"
      outbound_auth="$auth"

      if [ -n "$query" ]; then
        obfs=$(echo "$query" | grep -o 'obfs=[^&]*' | cut -d= -f2 || true)
        [ -n "$obfs" ] && outbound_obfs_password="$obfs"
        sni=$(echo "$query" | grep -o 'sni=[^&]*' | cut -d= -f2 || true)
        [ -n "$sni" ] && outbound_sni="$sni"
        ins=$(echo "$query" | grep -o 'insecure=[^&]*' | cut -d= -f2 || true)
        [ "$ins" = "1" ] || [ "$ins" = "true" ] && outbound_insecure="true"
      fi
      [ -z "$outbound_sni" ] && outbound_sni="$outbound_server"
      ;;

    socks5|socks)
      if [[ "$content" == *"@"* ]]; then
        user_pass="${content%%@*}"
        host_port="${content#*@}"
        if [[ "$user_pass" == *":"* ]]; then
          outbound_username="${user_pass%:*}"
          outbound_password2="${user_pass#*:}"
        fi
      else
        host_port="$content"
      fi
      outbound_server="${host_port%:*}"
      outbound_port="${host_port#*:}"
      outbound_type="socks"
      ;;

    *)
      echo "[ERROR] 不支持的协议: $proto"
      return 1
      ;;
  esac

  if [ -z "$outbound_server" ] || [ -z "$outbound_port" ]; then
    echo "[ERROR] 无法解析服务器地址或端口"
    return 1
  fi

  # 构建 outbound JSON
  jq_outbound="{\"type\":\"$outbound_type\",\"tag\":\"proxy\",\"server\":\"$outbound_server\",\"server_port\":$outbound_port"

  case "$outbound_type" in
    vless)
      jq_outbound="$jq_outbound,\"uuid\":\"$outbound_uuid\""
      [ -n "$outbound_flow" ] && jq_outbound="$jq_outbound,\"flow\":\"$outbound_flow\""
      if [ "$outbound_transport_type" != "tcp" ]; then
        jq_outbound="$jq_outbound,\"transport\":{\"type\":\"$outbound_transport_type\",\"path\":\"$outbound_path\",\"headers\":{\"Host\":\"$outbound_host\"}}"
      fi
      tls_enabled="false"
      [ "$outbound_security" = "tls" ] || [ "$outbound_security" = "reality" ] && tls_enabled="true"
      tls_json="{\"enabled\":$tls_enabled,\"server_name\":\"$outbound_sni\",\"insecure\":$outbound_insecure,\"utls\":{\"enabled\":true,\"fingerprint\":\"$outbound_fingerprint\"}"
      [ "$outbound_security" = "reality" ] && tls_json="$tls_json,\"reality\":{\"enabled\":true,\"public_key\":\"$outbound_reality_pbk\",\"short_id\":\"$outbound_reality_sid\"}"
      tls_json="$tls_json}"
      jq_outbound="$jq_outbound,\"tls\":$tls_json"
      ;;

    vmess)
      jq_outbound="$jq_outbound,\"uuid\":\"$outbound_uuid\",\"security\":\"auto\""
      jq_outbound="$jq_outbound,\"transport\":{\"type\":\"$outbound_transport_type\",\"path\":\"$outbound_path\",\"headers\":{\"Host\":\"$outbound_host\"}}"
      tls_enabled="false"
      [ "$outbound_security" = "tls" ] && tls_enabled="true"
      jq_outbound="$jq_outbound,\"tls\":{\"enabled\":$tls_enabled,\"server_name\":\"$outbound_sni\",\"insecure\":$outbound_insecure,\"utls\":{\"enabled\":true,\"fingerprint\":\"$outbound_fingerprint\"}}"
      ;;

    trojan)
      jq_outbound="$jq_outbound,\"password\":\"$outbound_password\""
      jq_outbound="$jq_outbound,\"transport\":{\"type\":\"$outbound_transport_type\",\"path\":\"$outbound_path\",\"headers\":{\"Host\":\"$outbound_host\"}}"
      jq_outbound="$jq_outbound,\"tls\":{\"enabled\":true,\"server_name\":\"$outbound_sni\",\"insecure\":$outbound_insecure,\"utls\":{\"enabled\":true,\"fingerprint\":\"$outbound_fingerprint\"}}"
      ;;

    hysteria2)
      jq_outbound="$jq_outbound,\"up_mbps\":$outbound_up_mbps,\"down_mbps\":$outbound_down_mbps"
      [ -n "$outbound_obfs_password" ] && jq_outbound="$jq_outbound,\"obfs\":{\"type\":\"salamander\",\"password\":\"$outbound_obfs_password\"}"
      [ -n "$outbound_auth" ] && jq_outbound="$jq_outbound,\"password\":\"$outbound_auth\""
      jq_outbound="$jq_outbound,\"tls\":{\"enabled\":true,\"server_name\":\"$outbound_sni\",\"insecure\":$outbound_insecure}"
      ;;

    socks)
      [ -n "$outbound_username" ] && jq_outbound="$jq_outbound,\"username\":\"$outbound_username\""
      [ -n "$outbound_password2" ] && jq_outbound="$jq_outbound,\"password\":\"$outbound_password2\""
      jq_outbound="$jq_outbound,\"version\":\"$outbound_version\""
      ;;
  esac
  jq_outbound="$jq_outbound}"

  # 生成 sing-box 配置
  cat << EOF > sing-box-config.json
{
  "log": {"level": "warn"},
  "inbounds": [
    {"type": "socks", "tag": "socks-in", "listen": "127.0.0.1", "listen_port": 1080},
    {"type": "http", "tag": "http-in", "listen": "127.0.0.1", "listen_port": 1081}
  ],
  "outbounds": [$jq_outbound]
}
EOF

  if ! jq empty sing-box-config.json 2>/dev/null; then
    echo "[ERROR] 生成的配置无效"
    return 1
  fi
  return 0
}

# ---- 清理 sing-box 进程 ----
stop_singbox() {
  pkill -f sing-box 2>/dev/null || true
  fuser -k 1080/tcp 2>/dev/null || true
  sleep 2
}

# ---- 测试本地代理连通性 ----
test_proxy() {
  local max_time=$1
  curl -x socks5://127.0.0.1:1080 -s --max-time "$max_time" https://api.ipify.org > /dev/null 2>&1
}

# ---- 逐节点测试，自动选择第一个可用节点 ----
SELECTED=""
for node in "${NODE_ARRAY[@]}"; do
  NODE_LINK="$node"
  echo ""
  echo "======================================================"
  echo "[INFO] 尝试节点: $(display_node "$node")"
  echo "======================================================"

  if ! generate_singbox_config; then
    echo "[FAILED] ❌ 节点解析失败，尝试下一个..."
    continue
  fi
  echo "[INFO] 服务器: ${outbound_server}:${outbound_port}"

  stop_singbox
  echo "[INFO] 启动 sing-box..."
  ./sing-box run -c sing-box-config.json > sing-box.log 2>&1 &
  sleep 5

  if ! pgrep -f sing-box > /dev/null; then
    echo "[FAILED] ❌ sing-box 启动失败，尝试下一个..."
    cat sing-box.log
    continue
  fi

  echo "[INFO] 测试节点连通性..."
  if test_proxy 10; then
    echo "[SUCCESS] ✅ 节点可用: $(display_node "$node")"
    SELECTED="$node"
    break
  fi

  echo "[FAILED] ❌ 节点连接失败，尝试下一个..."
  cat sing-box.log
done

if [ -z "$SELECTED" ]; then
  echo "[ERROR] ❌ 所有代理节点均连接失败"
  exit 1
fi

# ---- 最终验证并保持 sing-box 运行 ----
echo ""
echo "[INFO] 选中节点: $(display_node "$SELECTED")"
OK=""
for i in {1..3}; do
  if test_proxy 15; then
    OK="yes"
    break
  fi
  echo "[WARN] 最终验证尝试 $i/3..."
  sleep 3
done

if [ -z "$OK" ]; then
  echo "[ERROR] ❌ 代理最终验证失败"
  cat sing-box.log
  exit 1
fi

echo "[SUCCESS] ✅ 代理连接成功"
echo "PROXY_SERVER=socks5://127.0.0.1:1080" >> $GITHUB_ENV
exit 0
