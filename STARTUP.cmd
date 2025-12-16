@echo off
chcp 65001
cls
echo ==========================================
echo      正在啟動高雄旅遊智慧規劃助手...
echo ==========================================
echo.

echo [系統] 正在檢查並補齊必要套件...
echo (這可能需要幾秒鐘，請稍候)
pip install streamlit pandas numpy scikit-learn altair fpdf folium streamlit-folium requests datetime

echo.
echo [系統] 套件檢查完成，準備啟動！
echo.

:: 檢查 app.py 是否存在
if not exist app.py (
    echo [錯誤] 找不到 app.py 檔案！
    echo 請確保此啟動檔案與 app.py 位於同一個資料夾內。
    echo.
    pause
    exit
)

:: 執行程式
streamlit run app.py

:: 如果程式異常結束，暫停視窗讓使用者看錯誤訊息
if %errorlevel% neq 0 (
    echo.
    echo [錯誤] 程式執行失敗。請檢查上方的錯誤訊息。
    pause
)