# 沙盒的映像檔：agent 寫的 Python 程式在這個映像檔的容器裡執行。
#
# `docker compose up -d` 會一併建置。要加裝套件時改這個檔案，再執行
# `docker compose build sandbox-image`，並更新 docker-compose.yml 裡的
# HARNESS_SANDBOX_NOTES，模型才知道有哪些套件可以用。
FROM python:3.12-slim

# LibreOffice 讓網頁把 Word、PowerPoint 轉成 PDF 預覽（見
# harness.web.office），agent 也可以用它轉檔。fonts-liberation 的字型和
# Arial、Times New Roman 等寬，版面才不會跑掉。
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fonts-noto-cjk fonts-liberation \
      libreoffice-writer-nogui libreoffice-impress-nogui \
 && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir \
      pandas numpy openpyxl pdfplumber python-docx python-pptx matplotlib
# 圖表預設用中文字型，否則中文會變成方框。
RUN printf 'font.family: Noto Sans CJK JP\naxes.unicode_minus: False\n' \
      > /etc/matplotlibrc
ENV MATPLOTLIBRC=/etc/matplotlibrc
