# Agent Harness 的網頁服務（Python 版）。
#
# 一般不需要直接用這個檔案：在 repo 根目錄執行 `docker compose up -d`
# 就會建置並啟動，說明見文件網站的「快速部署」（https://agent-harness-docs-ten.vercel.app/guide/deploy）。
#
# 公司網路要透過 proxy 才能連外時，把 proxy 寫在 .env（見 .env.example），
# docker compose 會在建置時與執行時都帶上。proxy 不會寫進映像檔。
FROM python:3.12-slim

# 立刻印出訊息，`docker compose logs` 才看得到啟動時的網址與登入密碼。
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 沙盒用主機的 Docker 啟動容器，這裡只需要 docker 指令本身。
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

COPY python /tmp/harness
RUN pip install "/tmp/harness[mcp,pdf,vectors]" && rm -rf /tmp/harness

# 用一般使用者執行；專案與所有資料放在 /assistant（掛載 volume）。
RUN useradd --create-home --uid 1000 harness \
 && mkdir /assistant \
 && chown harness:harness /assistant
COPY docker/entrypoint.sh /usr/local/bin/harness-entrypoint
RUN chmod +x /usr/local/bin/harness-entrypoint

VOLUME /assistant
EXPOSE 4321
ENTRYPOINT ["harness-entrypoint"]
# /assistant 是空的時，會自動建立一個可以直接使用的助手。
CMD ["pyharness", "web", "-p", "/assistant", "--host", "0.0.0.0"]
