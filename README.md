# RF Localization — hướng dẫn chạy và đo thực tế

4 ESP32 thu RSSI thô và gửi MQTT về laptop. Gateway Python lưu SQLite, tính
thống kê RSSI, ghép vector 4 node cùng session/epoch/target rồi chạy WCL và hiển
thị Dashboard. README này gom cách chạy đã thực hiện trên Windows, các lỗi đã
gặp và quy trình thu dữ liệu thực tế.

Để hiểu source, đọc mục 13 (luồng chạy), 14 (từng file), 15–18 (từng hàm C++,
Python và JavaScript), 19 (vai trò __init__.py và các bảng SQLite).

## 1. Chạy lại nhanh khi đã cấu hình ESP32

Mở VS Code → Terminal → New Terminal. Dùng **hai terminal riêng**, giữ cả hai chạy.

**Terminal 1 — Mosquitto:**

~~~powershell
cd "C:\Users\Admin\Downloads\do an"
& "C:\Program Files\mosquitto\mosquitto.exe" -c ".\scripts\mosquitto.conf" -v
~~~

Nếu cổng bị chiếm hoặc broker chỉ nghe localhost, xử lý theo mục 5.

**Terminal 2 — Gateway:**

~~~powershell
cd "C:\Users\Admin\Downloads\do an"

$env:MQTT_HOST = "127.0.0.1"
$env:MQTT_PORT = "1883"
$env:TARGET_DEVICE_MAC = "66:C2:DE:00:BE:87"
$env:TARGET_DEVICE_TOKEN = ""
$env:DEVICE_TOKEN_SALT = "secret-key"
$env:LOCALIZE_TARGET_ONLY = "true"
$env:MIN_OVERLAP_MS = "600"
$env:EPOCH_TIMEOUT_MS = "1800"
$env:DATABASE_PATH = "database/rf_gateway.db"

& .\.venv\Scripts\python.exe -m uvicorn main:app --app-dir gateway --host 0.0.0.0 --port 8000
~~~

Cấp nguồn 4 ESP32, mở **http://127.0.0.1:8000**, đặt Session ID là
**session-dev-001**, giống cả 4 node. Chờ đủ 4 node online, synced và
complete / Nodes Used = 4 trước khi đo.

BSSID target phía trên là giá trị của mạng đã dùng; kiểm tra lại nếu hotspot
đổi. Biến môi trường chỉ áp dụng cho terminal hiện tại, đặt lại khi mở terminal
mới. Không cần activate venv khi dùng đường dẫn python.exe đầy đủ.

**Không chạy lệnh này từ thư mục cha “do an”. Activate không tự đổi thư mục.**
Nếu chưa cài venv hoặc chưa cấu hình node, làm lần lượt các mục dưới.

## 2. Thiết bị và sơ đồ mạng

~~~text
Điện thoại: hotspot 2.4 GHz + target của bài thử
    ├── ESP01 ──┐
    ├── ESP02 ──┤ gửi RSSI qua MQTT
    ├── ESP03 ──┤
    ├── ESP04 ──┘
    └── Laptop: Mosquitto :1883 + Gateway/Dashboard :8000
~~~

- Hotspot cần nhận ít nhất **5 thiết bị đồng thời**: 4 ESP32 và laptop.
- Bật dữ liệu di động cho ESP32 đồng bộ NTP; chọn hotspot **2.4 GHz**.
  Nếu có tự tắt hotspot thì tắt trong buổi đo. Trên iPhone có hỗ trợ, bật
  Maximize Compatibility / Tối đa hóa tương thích.
- USB dùng để flash, xem Serial và cấp nguồn; dữ liệu đo đi qua Wi-Fi.
- Bài này định vị **điện thoại đang phát hotspot**. Nếu định vị điện thoại khác,
  phải xác định đúng MAC phát frame của thiết bị đó trên kênh đang thu.
- Giữ laptop cố định, hotspot bật và nguồn ESP32 ổn định.

Firmware hiện dùng board **esp32dev** cho ESP32 DevKit/WROOM-32 thông thường.
Với C3/S2/S3 hoặc board khác, kiểm tra lại board trước khi flash. Wi-Fi, MQTT,
Node ID, session và target nhập bằng web, không cần sửa source trước khi flash.

## 3. Xác định IP laptop và MAC target

Kết nối laptop vào hotspot, chạy:

~~~powershell
ipconfig
netsh wlan show interfaces
~~~

Lấy IPv4 của adapter Wi-Fi và BSSID của đúng hotspot.

| Thông tin đã ghi nhận | Giá trị | Dùng làm gì |
|---|---|---|
| SSID | V50 | Wi-Fi của các node |
| IPv4 laptop | 192.168.87.160 | mqtt_host trên ESP32 |
| Default Gateway | 192.168.87.233 | Điện thoại/hotspot, không phải broker |
| BSSID | 66:C2:DE:00:BE:87 | target_mac khi đo điện thoại phát hotspot |
| Channel | 11 | Kênh 2.4 GHz đã dùng |

Đây là thông tin của lần chạy trước, **không cố định**. Kiểm tra lại sau khi
tắt/bật hotspot, đổi mạng hoặc laptop kết nối lại. Nếu Windows yêu cầu quyền
vị trí để đọc WLAN, làm theo thông báo rồi chạy lại.

Không dùng Physical address của laptop làm target trong bài này. BSSID hotspot
có thể khác MAC Wi-Fi trong phần giới thiệu điện thoại. Không nhập Default
Gateway hoặc 127.0.0.1 vào mqtt_host trên ESP32.

## 4. Python và venv

Máy đã dùng có Python, PlatformIO và Mosquitto. Nếu chuyển máy, cài các công cụ
tương ứng rồi điều chỉnh đường dẫn. Các lệnh tương đối dưới đây chạy tại:

~~~powershell
cd "C:\Users\Admin\Downloads\do an"
~~~

### Tạo venv và cài thư viện lần đầu

~~~powershell
python --version
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\gateway\requirements.txt
& .\.venv\Scripts\python.exe -c "import fastapi, uvicorn, paho.mqtt.client, pydantic; print('Thu vien OK')"
~~~

Venv đang chạy tốt thì dùng lại; không cần tạo hoặc cài lại mỗi lần. Có thể
activate nếu muốn dùng lệnh python ngắn:

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& .\.venv\Scripts\Activate.ps1
~~~

Activate không đổi thư mục hiện tại. Thoát môi trường bằng deactivate.

### Xóa venv cũ bị lỗi

1. Dừng Gateway và các chương trình đang dùng đúng venv này bằng Ctrl+C.
2. Nếu đang activate, chạy deactivate.
3. Kiểm tra đường dẫn rồi xóa đúng môi trường:

~~~powershell
Get-Item -Force "C:\Users\Admin\Downloads\do an\.venv"
Remove-Item -LiteralPath "C:\Users\Admin\Downloads\do an\.venv" -Recurse -Force
~~~

4. Tạo lại venv và cài requirements như phía trên.

Lỗi Access denied với python.exe hoặc .pyd thường do Gateway vẫn chạy và khóa
file. **Deactivate không dừng Gateway.** Không tắt hàng loạt Python của
PlatformIO/chương trình khác. Lần xóa thất bại có thể đã xóa một phần venv; sau
khi dừng chương trình hãy xóa phần còn lại rồi tạo lại.

Source và database ngoài .venv không bị xóa; thư viện trong venv phải cài lại.

## 5. Mosquitto, localhost và firewall

Cấu hình có sẵn trong scripts/mosquitto.conf:

~~~text
listener 1883
listener_allow_anonymous true
socket_domain ipv4
~~~

Cấu hình đã dùng với Mosquitto 2.1.2 trong lần chạy này. Nếu máy khác báo unknown
configuration variable, kiểm tra phiên bản/cấu hình tương ứng. Broker anonymous
này chỉ dùng trong mạng đo tin cậy.

### Dừng dịch vụ đang chiếm cổng

Mở PowerShell **Run as administrator**:

~~~powershell
Get-Service *mosquitto*
~~~

Nếu dịch vụ mosquitto đang Running và muốn dùng broker của project:

~~~powershell
Stop-Service -Name mosquitto
~~~

Chạy Mosquitto ở Terminal 1 theo mục 1, hoặc chọn script thay thế:

~~~powershell
.\scripts\start_mqtt.bat
~~~

Chỉ chạy **một** broker. Không chạy thêm script nếu đã dùng lệnh trực tiếp.
Giữ terminal broker mở. Dịch vụ có thể chạy lại khi reboot Windows.

Kiểm tra ở terminal khác:

~~~powershell
netstat -ano -p tcp | Select-String ':1883'
~~~

Cần LISTENING ở **0.0.0.0:1883** hoặc **IP Wi-Fi laptop:1883**.
Nếu chỉ thấy **127.0.0.1:1883**, ESP32 không truy cập được. Đây là lỗi đã gặp:
dịch vụ Mosquitto chạy nền chỉ nghe localhost; mở firewall không sửa listener.
Dừng đúng instance và chạy broker với -c scripts/mosquitto.conf.

### Firewall

Trong PowerShell administrator, tạo rule một lần nếu chưa có:

~~~powershell
New-NetFirewallRule -DisplayName "RF MQTT 1883" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 1883 -RemoteAddress LocalSubnet -Profile Any
~~~

Rule RF MQTT 1883 đã được tạo trong lần cấu hình này, không cần tạo lặp.
Không cần tắt toàn bộ firewall. Có thể kiểm tra TCP bằng IP laptop hiện tại:

~~~powershell
Test-NetConnection 192.168.87.160 -Port 1883
~~~

TcpTestSucceeded=True chỉ kiểm tra từ máy đang chạy lệnh; vẫn cần xác nhận
mqtt_connected=true trên từng ESP.

Chuyển Mosquitto từ cửa sổ PowerShell ngoài vào VS Code: Ctrl+C ở cửa sổ cũ,
Terminal → New Terminal trong VS Code rồi chạy broker lại. Không cần activate
venv cho Mosquitto.

## 6. Flash từng ESP32

Cắm một board bằng cáp USB có truyền dữ liệu. Ví dụ board dùng COM5:

~~~powershell
cd "C:\Users\Admin\Downloads\do an"

& "C:\Users\Admin\.platformio\penv\Scripts\platformio.exe" device list
& "C:\Users\Admin\.platformio\penv\Scripts\platformio.exe" run -d firmware
& "C:\Users\Admin\.platformio\penv\Scripts\platformio.exe" run -d firmware -t upload --upload-port COM5
& "C:\Users\Admin\.platformio\penv\Scripts\platformio.exe" device monitor --port COM5 --baud 115200
~~~

Thay COM5 đúng cổng board. Nếu không thấy log, nhấn EN/RESET. Một số board cần
giữ BOOT lúc uploader Connecting, thả khi bắt đầu ghi. Ctrl+C đóng Serial trước
khi flash lại cổng đó.

Cùng một firmware dùng cho cả 4 node. Flash thường giữ lại NVS cũ; không cần
flash lại để đổi Wi-Fi, broker hoặc target.

## 7. Cấu hình web AP → STA

Khi chưa có cấu hình, Serial hiện dạng:

~~~text
[CONFIG] Provisioning AP=RF-Setup-... IP=192.168.4.1
~~~

1. Giữ hotspot điện thoại bật. Laptop tạm ngắt hotspot, kết nối RF-Setup-... của
   một ESP. Chấp nhận mạng không có Internet.
2. Mở http://192.168.4.1, nhập bảng bên dưới.
3. Save and restart: ESP lưu NVS, trả response rồi reboot kết nối hotspot.
4. Laptop kết nối lại hotspot; kiểm tra IPv4 chưa đổi.
5. Ghi IP STA từ Serial; lần sau truy cập IP đó để sửa cấu hình.

| Trường | Giá trị |
|---|---|
| node_id | ESP01, ESP02, ESP03 hoặc ESP04, riêng từng board |
| wifi_ssid | V50 hoặc hotspot hiện tại |
| wifi_password | Tự nhập mật khẩu hotspot |
| mqtt_host | 192.168.87.160, thay nếu IP laptop đổi |
| mqtt_port | 1883 |
| session_id | session-dev-001, giống cả 4 node và Dashboard |
| target_mac | 66:C2:DE:00:BE:87, thay nếu BSSID đổi |
| capture_mode | target_only |
| epoch_ms | 1000 |
| sniff_window_ms | 700 |
| mqtt_batch_max_samples | 16 |
| mqtt_batch_max_bytes | 4096 |

Không chọn Open Wi-Fi nếu hotspot có mật khẩu. Khi sửa qua form, để trống ô
password để giữ giá trị cũ; chỉ chọn Open Wi-Fi khi muốn xóa mật khẩu Wi-Fi.
Cấu hình lần lượt 4 board và dán nhãn để tránh trùng node_id.

IP đã ghi lại từ lần chạy, có thể đổi theo DHCP:

| Node | Trang cấu hình |
|---|---|
| ESP01 | http://192.168.87.208 |
| ESP02 | http://192.168.87.103 |
| ESP03 | http://192.168.87.49 |
| ESP04 | http://192.168.87.61 |

Trang cấu hình không yêu cầu đăng nhập trên cả AP và STA. Mật khẩu Wi-Fi vẫn
được lưu trong NVS; GET /api/config và Export không trả mật khẩu này.

Nếu kết nối STA thất bại, sau ba đợt chờ khoảng 15 giây sẽ fallback AP, giữ NVS.
Nếu cần xóa cấu hình, giữ BOOT/GPIO0 ít nhất 3 giây **sau khi chương trình đã
boot** để factory reset. Không giữ BOOT ngay lúc cấp nguồn nếu muốn chạy bình
thường. Factory reset xóa cấu hình; restart thường giữ NVS. Đổi session/target
qua web sẽ save rồi reboot, không trộn batch cũ sang cấu hình mới.

Web config không có mật khẩu: người truy cập được IP ESP có thể sửa cấu hình,
restart hoặc factory reset khi xác nhận. Chỉ dùng trong mạng tin cậy. Kiểm tra
Origin, JSON, input validation và xác nhận factory reset vẫn được giữ.

Cấu hình NVS cũ vẫn đọc được sau khi flash bản bỏ đăng nhập; không cần reset
Wi-Fi/session/target. Nhấn Save một lần để ghi lại blob NVS không còn trường
mật khẩu admin cũ. Ô Wi-Fi password để trống trên form sẽ giữ mật khẩu đã lưu.

## 8. Kiểm tra node và chạy Dashboard

Mở trực tiếp http://IP-NODE/api/status, không cần đăng nhập. Cả 4 node phải có:

~~~text
mode: STA
wifi_connected: true
mqtt_connected: true
clock_synced: true
~~~

captured_sample_count tăng; queue không đầy liên tục. Theo dõi queue_drop_count,
batch_drop_count, marker_drop_count, mqtt_publish_fail_count, free_heap và latency.
Counters cộng dồn từ boot, không tự giảm khi sửa lỗi. Có thể restart sau khi
broker ổn định để xem counters mới, rồi chờ NTP sync lại.

ESP kết nối MQTT được khi **chỉ broker chạy**, chưa cần Gateway Python.
Dashboard mở được cũng không chứng minh ESP đã vào MQTT.

NTP giữ configTime với pool.ntp.org/time.nist.gov. Chưa sync thực sự thì Unix=0,
clock_synced=false và Gateway chặn WCL. Laptop vào web được chưa bảo đảm NTP
qua mạng đó được thông.

Chạy Gateway theo mục 1 trong terminal riêng. Phân biệt:

| Nơi cấu hình | MQTT host |
|---|---|
| ESP32 | IP Wi-Fi laptop, ví dụ 192.168.87.160 |
| Gateway cùng laptop với broker | 127.0.0.1 |

Khi thấy Uvicorn running, mở http://127.0.0.1:8000. Nhập kích thước phòng, kích
thước ô và Session ID=session-dev-001, Apply room + grid. Cần thấy 4 node online,
Nodes Used=4, vector đủ 4 RSSI, epoch tăng và quality complete.

~~~text
EPOCH session=session-dev-001 epoch=... status=complete overlap=...ms
LOCALIZATION epoch=... nodes=4 vector={'ESP01': ..., 'ESP02': ..., 'ESP03': ..., 'ESP04': ...}
~~~

### Chọn đúng database và cấu hình môi trường

Database mặc định giờ luôn là **database/rf_gateway.db** tính từ vị trí source
project, không phụ thuộc thư mục terminal. Thư mục được tự tạo khi mở database.

~~~powershell
$env:DATABASE_PATH = "database/rf_gateway.db"
~~~

Có thể bỏ hẳn biến DATABASE_PATH để dùng mặc định. Muốn tách một buổi đo:

~~~powershell
$env:DATABASE_PATH = "buoi-do-002.db"
~~~

File đó nằm ở database/buoi-do-002.db. Đường dẫn tương đối có tiền tố database/
cũng được hỗ trợ, không bị lặp database/database. Đường dẫn tuyệt đối tùy chỉnh
vẫn là override có chủ ý nếu thực sự cần lưu ngoài thư mục này.

Các cấu hình mặc định cũ rf_gateway.db, gateway/rf_gateway.db và đường dẫn tuyệt
đối tương ứng của project được chuẩn hóa sang database/rf_gateway.db. Đây là
chuẩn hóa nơi ghi **từ lần khởi động mới**, không phải tự chuyển/gộp lịch sử.

Hai file trước đây có thể chứa dữ liệu khác nhau vì code cũ dùng đường dẫn
tương đối theo thư mục chạy lệnh. Khi kiểm tra workspace hiện tại trước thay
đổi này, không còn thấy file .db cũ ở gốc/gateway để chuyển. Nếu còn bản cũ ở chỗ
khác: dừng Gateway, giữ nguyên bản gốc, dùng SQLite backup để đưa thành các tên
riêng trong database/ rồi chọn file cần dùng. Không copy đè file đang có dữ liệu
hoặc tự gộp bảng khi chưa kiểm tra khóa/trùng dữ liệu.

Khi Gateway khởi động, log dòng SQLite database: cho biết file thực tế;
GET /health cũng có database_path. Gateway đang chạy phải restart để nhận code
và biến môi trường mới. Firmware ESP32 không cần flash lại vì thay đổi này.

Có thể sao chép gateway/.env.example thành gateway/.env rồi chỉnh cấu hình;
python-dotenv tìm .env theo cơ chế của thư viện, biến môi trường đã có được ưu
tiên. Nếu dùng MAC, để TARGET_DEVICE_TOKEN trong .env trống vì token được ưu
tiên hơn MAC. .env bị ignore để tránh commit secrets.

scripts/start_gateway.bat dùng python của terminal: nếu dùng script, activate
đúng venv và đặt biến trước. Lệnh trực tiếp ở mục 1 giúp rõ interpreter/cwd.

## 9. Bố trí và đo thực tế

~~~text
ESP01 (0,0)                         ESP02 (width,0)
      +-----------------------------+
      |           vùng đo           |
      +-----------------------------+
ESP03 (0,length)               ESP04 (width,length)
~~~

1. Cố định 4 node ở bốn góc, nguồn ổn định, độ cao/hướng anten tương đối thống nhất.
   Giữ laptop cố định, tránh anten sát kim loại.
2. Đo kích thước phòng thật và nhập Dashboard. Gốc tọa độ ở ESP01.
3. Đánh dấu khoảng 5 điểm, ghi tọa độ thật (giữa phòng và các điểm xung quanh).
4. Đặt điện thoại tại mỗi điểm, giữ độ cao/hướng tương tự; chờ ổn định khoảng
   10 giây rồi thu 30–60 giây. Ghi epoch bắt đầu/kết thúc từ Dashboard.
5. Giữ mức sử dụng mạng tương đối giống nhau, hotspot và cả 5 client kết nối ổn
   định. Không đổi cấu hình/session giữa đoạn đo.
6. Ghi ground truth riêng, ví dụ CSV:
   session_id,point_id,x_m,y_m,start_epoch,end_epoch.
   Gateway chưa tự nhập nhãn này, người đo ghi để ghép khi phân tích.

Confidence trên UI không phải phần trăm độ chính xác thực tế. WCL dùng median
RSSI, weight_i=10^((RSSI_i-max_RSSI)/10), không phải mô hình khoảng cách đã hiệu
chuẩn. Đánh giá bằng so tọa độ ước lượng với ground truth.

ESP32 chỉ có một radio: hai core không bảo đảm thu 100% frame khi truyền MQTT.
Queue drops chỉ đo mất mẫu phía phần mềm; mất frame trước callback cần nguồn
phát/reference capture có số frame biết trước mới đánh giá được.

## 10. Xuất dữ liệu và kết thúc

Giữ Gateway chạy khi xuất, mở terminal khác:

~~~powershell
cd "C:\Users\Admin\Downloads\do an"

Invoke-WebRequest "http://127.0.0.1:8000/api/v1/raw/export?session_id=session-dev-001" -OutFile "raw_session-dev-001.jsonl"

Invoke-WebRequest "http://127.0.0.1:8000/api/v1/epochs/quality?limit=200" -OutFile "quality_latest_200.json"
~~~

JSONL giữ raw chunks theo session, từng mẫu RSSI/timestamp/token. File quality
chỉ lấy **200 epoch gần nhất**, database giữ quality đã lưu.

Giữ JSONL, đúng database đã dùng, ground truth và ghi chú bố trí. Xuất xong
Ctrl+C dừng Gateway; nếu copy database hãy đợi Gateway thoát hẳn để tránh còn
đang ghi. Sau đó Ctrl+C dừng broker.

Buổi mới nên đổi session trên cả 4 node và Dashboard, ví dụ session-do-002,
đổi session_id trong URL export cho khớp. Có thể dùng chung database hoặc
chọn file mới nhưng phải ghi rõ file/session của buổi đo.

## 11. Lỗi đã gặp

| Hiện tượng | Xử lý |
|---|---|
| Không nhận .\.venv\Scripts\python.exe | cd vào project, không đứng ở do an. Nếu vẫn thiếu, kiểm tra venv có bị xóa dở không. |
| Không có uvicorn/fastapi | Cài gateway/requirements.txt bằng đúng python.exe của .venv. |
| Xóa venv Access denied | Dừng Gateway đang khóa file; deactivate không đủ. Sau đó xóa phần còn lại và tạo lại. |
| MQTT false, Wi-Fi/NTP true | Kiểm tra mqtt_host là IP laptop, broker nghe LAN, cổng/firewall. |
| Broker chỉ nghe 127.0.0.1 | Dừng dịch vụ/instance chiếm cổng, chạy với -c scripts/mosquitto.conf. |
| Address already in use | Có broker khác dùng 1883; kiểm tra netstat, dừng đúng instance. |
| Queue 512/512, drops tăng | Chưa đo. Sửa kết nối/tải publisher; quan sát sau restart và sync lại. Không tăng buffer để che lỗi. |
| IP node không mở được | Kiểm tra laptop cùng hotspot, IP STA mới trong Serial hoặc node đã fallback AP. |
| 4 node online nhưng chưa có vị trí | Xem /api/v1/epochs/quality; online không đồng nghĩa vector hợp lệ. |
| missing_node | Node/session/target không khớp hoặc node không thấy target. |
| missing_chunk | Kiểm tra MQTT, tải truyền và counters. |
| unsynced | Kiểm tra NTP từng node. |
| low_overlap | Kiểm tra cùng epoch/window và thời gian mở window; không hạ ngưỡng chỉ để có vị trí. |
| empty_target | Kiểm tra BSSID hiện tại và target_only trên các node. |
| Lịch sử không đúng | Kiểm tra Session ID và DATABASE_PATH, có thể đang mở database khác. |

Nếu cùng hotspot, listener/firewall đều đúng nhưng ESP vẫn không vào broker,
kiểm chứng hotspot có hạn chế liên lạc giữa các client hay không; cần mạng
cho phép client liên lạc. Laptop vào Internet không đủ để kết luận.

## 12. Source và quy tắc xử lý

~~~text
do an/
  README.md
  database/
    .gitkeep
    rf_gateway.db
  firmware/
    platformio.ini
    src/config.h
    src/main.cpp
    src/web_config.h
    src/web_config.cpp
  gateway/
    .env.example
    requirements.txt
    __init__.py
    config.py
    models.py
    mqtt_ingest.py
    epoch_reconstructor.py
    localization.py
    database.py
    main.py
    static/index.html
  scripts/
    mosquitto.conf
    start_mqtt.bat
    start_gateway.bat
~~~

Ngoài source còn có .venv, build cache, database và backup cục bộ tùy máy.
Database là dữ liệu đo, không phải file build thừa.

- Callback Wi-Fi do ESP-IDF quản lý → queue nonblocking → publisher Core 1 trên
  dual-core (Core 0 nếu UNICORE). ESP không tính median/mean/MAD; giữ SNTP,
  STA + promiscuous và MQTT QoS 0.
- Track transmitter address (TA), phân biệt với source logic: RSSI thuộc thiết bị
  thực sự phát radio. TA của management/data hỗ trợ là addr2, source có thể ở
  addr3/addr4. AP uplink chỉ được theo dõi khi là target.
- Token là SHA256(MAC bytes + salt); salt giống cả 4 node và Gateway. MAC có thể
  ngẫu nhiên, không là danh tính vật lý vĩnh viễn.
- Topic rf/v1/nodes/{node_id}/observation, schema_version=2. Mỗi mẫu có token,
  rssi_dbm, channel, frame_type/subtype, capture_mono_us và capture_unix_ms.
  Batch có session/node/epoch, epoch_ms, clock_synced, cửa sổ UTC, chunk_index/
  chunk_count và counters. Empty epoch có observations rỗng.
- Queue 512 records, buffer 512 mẫu/epoch; mặc định 16 mẫu/chunk, 4096 JSON bytes.
  Chia theo cả mẫu/byte. Window bị ngắt hoặc mất marker không coi là hoàn tất.
  Mẫu vượt giới hạn/publish lỗi được đếm; không có backlog vô hạn.
- Gateway lưu raw trước thống kê, dedup session/epoch/node/chunk, ghép đủ chunks,
  tính count/median/mean/MAD/min/max, dùng median cho WCL.
- Strict_4: đủ ESP01–04, synced, cùng epoch duration, overlap >=600 ms mặc định;
  timeout 1800 ms. Overlap=max(0,min(end)-max(start)). Không điền RSSI thiếu.
  Tombstone SQLite chặn phát lại epoch đóng; buffer pending chưa đủ cần message
  gửi lại sau restart. V1 chỉ chẩn đoán; NODE_READY giữ v1.

API node: GET /, /api/status, /api/config; POST /api/config, /api/restart,
 /api/factory-reset. Factory reset cần confirm="factory-reset" trong JSON.
API Gateway: GET /health, /api/v1/nodes, /api/v1/room,
/api/v1/localization/latest, /api/v1/epochs/quality, /api/v1/raw/export;
POST /api/v1/room; WebSocket /ws/v1/sessions/{session_id}.

## 13. Giải thích luồng chạy từ tín hiệu tới chấm trắng trên Dashboard

### 13.1. Các khái niệm cần phân biệt

| Khái niệm | Hiểu đơn giản | Ví dụ |
|---|---|---|
| Node | Một ESP32 đứng ở một góc phòng | ESP01 |
| Target | Thiết bị phát tín hiệu muốn theo dõi | BSSID hotspot điện thoại |
| RSSI | Độ mạnh tín hiệu nhận được, đơn vị dBm | -50 mạnh hơn -70 |
| Sample | Một lần thu RSSI của một frame | -51 dBm lúc 12:00:00.120 |
| Session | Nhãn của một phiên đo | session-dev-001 |
| Epoch | Một ô thời gian chung để gom số đo | Mỗi ô 1000 ms |
| Batch | Các mẫu thô của một node trong một epoch | 7 mẫu của ESP01 |
| Chunk | Một phần batch, chia nhỏ để vừa MQTT | chunk 0 và chunk 1 |
| Device token | SHA256 của MAC bytes + salt, thay raw MAC trong MQTT | Chuỗi hex 64 ký tự |
| Vector RSSI | Mỗi node góp một median của cùng target/epoch | [-51,-60,-63,-70] |

Node ID là tên ESP; token là mã của target; session là tên buổi đo.
Ba loại tên này không thay thế nhau. Một epoch có thể có nhiều target và mỗi
target cần vector riêng. RSSI nhận ở các node có thể khác số mẫu, không cần
packet_count bằng nhau.

### 13.2. Ai làm việc gì?

~~~mermaid
flowchart TD
    A["Điện thoại phát frame Wi-Fi"] --> B["Callback ESP32: RSSI + timestamp capture"]
    B --> C["FreeRTOS Queue: giữ mẫu tạm trong RAM"]
    C --> D["Publisher task: batch, chunk, JSON raw"]
    D --> E["Mosquitto: chuyển tin MQTT"]
    E --> F["MqttGateway: parse + validate topic/payload"]
    F --> G["EpochReconstructor.add"]
    G --> H["SQLite raw_chunks: lưu trước thống kê"]
    G --> I["Đủ chunk/node hoặc timeout: đóng epoch"]
    I --> J["Tính count, median, mean, MAD, min, max"]
    J --> K{"Đủ 4 node, synced, overlap đạt?"}
    K -->|"Không"| L["Lưu quality; không phát vị trí mới"]
    K -->|"Có"| M["GatewayRuntime: chọn target rồi WCL"]
    M --> N["SQLite: evidence + localization"]
    M --> O["WebSocket: kết quả theo session"]
    O --> P["JavaScript: vẽ vị trí lên SVG"]
~~~

Mosquitto chỉ làm trung gian nhận/chuyển bản tin theo topic. Nó không tính
median và không định vị. SQLite là kho dữ liệu, không phải thuật toán định vị.
Dashboard hiển thị kết quả Gateway đã tính, không tự chạy WCL.

### 13.3. Từ khi ESP32 được cấp nguồn

1. Arduino gọi setup() một lần: mở Serial, loadConfig() từ NVS, tạo queue,
   chuẩn bị MQTT client và MAC target.
2. Chưa có SSID thì enterAP() để người dùng nhập cấu hình. Đã có SSID thì bắt
   đầu kết nối STA. startWebConfig() đăng ký các API; publisherTask chạy riêng.
3. Arduino gọi loop() lặp mãi: phục vụ web, kiểm tra nút BOOT, xử lý Wi-Fi.
4. STA kết nối: bắt đầu SNTP, đọc BSSID AP, bật promiscuous management/data.
   Sniffer chỉ nghe kênh của STA hiện tại.
5. SNTP báo thành công thì timeSynced() cập nhật phép đổi monotonic → UTC.
   Không tự báo synced chỉ vì Wi-Fi đã kết nối.
6. captureControl() mở cửa sổ đầu mỗi epoch. Callback chỉ nhận mẫu vào cửa sổ
   đang mở; sau thời hạn thì không nhận mẫu vào epoch cũ.
7. Wi-Fi driver gọi promiscuousCallback() khi nhận frame: kiểm tra header/MAC,
   lấy RSSI và timestamp, xQueueSend(...,0) để đẩy mẫu mà không đợi queue trống.
8. publisherTask() lấy mẫu ra, giữ batch và chờ marker kết thúc. publishBatch()
   chia chunk/serialize/publish; callback vẫn có thể thu vào queue trong lúc đó.

~~~text
Ví dụ mỗi epoch 1000 ms, sniff_window_ms=700:

epoch N:      |--- nhận mẫu khoảng 700 ms ---|--- khoảng còn lại ---|
epoch N+1:                                                        |--- ... ---

Driver callback: nhận RSSI khi frame đến, kiểm tra cửa sổ capture
Arduino loop:   mở/đóng cửa sổ + web + Wi-Fi
Publisher:      lấy queue + gửi batch đã đóng
~~~

Promiscuous không bị tắt riêng để publish; 300 ms còn lại không được nhận mẫu
vào batch theo cấu hình cửa sổ hiện tại. Capture bắt đầu thực tế có thể trễ hơn
ranh giới lý tưởng, vì thế startMs ghi thời điểm mở thật. Mất Wi-Fi làm window
bị abort, không dùng window đó để khẳng định thu đầy đủ.

Wi-Fi callback thuộc ngữ cảnh driver, không phải task ứng dụng tự pin core.
Publisher được pin Core 1 trên dual-core; Arduino loop giữ cấu hình core của
framework, không có Capture Task riêng pin Core 0 trong code hiện tại.
Queue tách công việc giữa các luồng; không tạo thêm radio Wi-Fi thứ hai.

Hai file .cpp hiện không còn khối anonymous namespace { }. Các biến/hàm trước
đây nằm trong khối đó giờ ở phạm vi toàn cục; phải tránh trùng tên giữa các file.
Thay đổi này không bỏ task hoặc đổi luồng capture/publish. Từ namespace trong
NVS rf-node là vùng lưu cấu hình, không phải khối namespace C++ vừa bỏ.

### 13.4. Timestamp và epoch

esp_timer_get_time() trả microseconds từ lúc chip chạy, riêng từng node.
SNTP cung cấp UTC; timeSynced() tính:

~~~text
unixOffsetUs = Unix time tại sync - monotonic time tại sync
capture_unix_ms = (capture_mono_us + unixOffsetUs) / 1000
epoch_id = floor(capture_unix_ms / epoch_ms)
~~~

Gắn thời gian khi callback nhận frame, không khi publisher lấy queue. Ví dụ
frame thu cuối epoch N nhưng chờ queue sang N+1 vẫn thuộc N.

Chưa synced: Unix=0, epoch chẩn đoán dựa monotonic; Gateway không dùng nó làm
vector đồng bộ chuẩn. Cờ synced và kiểm tra overlap không chứng minh sai số
đồng hồ giữa các chip bằng 0; hệ thống chưa đo sai số SNTP thực tế.

### 13.5. Gateway bắt đầu chạy thế nào?

Lệnh uvicorn main:app --app-dir gateway nạp gateway/main.py như module main:

1. Import config.py: đọc môi trường/.env và tạo DEFAULT_SETTINGS.
2. Dòng runtime = GatewayRuntime() chạy ngay lúc import: mở SQLite tại đường
   dẫn đã resolve, tạo/migrate bảng, đọc kích thước phòng đã lưu.
3. Runtime tạo localizer, WebSocketManager, EpochReconstructor và MqttGateway,
   nối chúng bằng các callback.
4. Uvicorn bắt đầu lifespan(): lấy asyncio event loop, khởi động Paho MQTT và
   tạo expiry_loop().
5. Paho có network thread riêng: nhận MQTT rồi gọi _on_message(). Asyncio loop
   phục vụ HTTP/WebSocket và kiểm tra timeout khoảng 100 ms/lần.
6. MQTT callback muốn gửi lên browser dùng run_coroutine_threadsafe() để giao
   việc broadcast về đúng asyncio event loop.

Lock trong reconstructor ngăn MQTT thread và expiry loop cùng sửa/đóng một
bucket. Lock trong Database bảo vệ một SQLite connection dùng giữa các thread.
Các lock không có nghĩa là phần mềm xử lý mọi việc đồng thời trên SQLite.

### 13.6. Ghép epoch, lưu raw và quyết định có chạy WCL không

_on_message() kiểm tra JSON/Pydantic/topic trước, rồi báo node seen và add(report).
Reconstructor dùng bucket (session_id, epoch_id), trong đó giữ chunks theo node.
Một chunk trùng không được cộng lại mẫu. Chunk cùng khóa nhưng khác nội dung là
conflict, bị từ chối. Chunk tới epoch đã đóng bị coi là late.

add() gọi on_raw → store_raw_chunk() trước khi đưa chunk vào phần tính thống kê.
Đủ tất cả chunk của 4 node thì đóng ngay; không đủ thì chờ timeout, không chờ mãi.

_emit() chỉ tính thống kê cho node đã đủ chunks, gom mẫu riêng theo device_token.
Ví dụ raw [-51,-49,-90]:

~~~text
packet_count = 3
median = -51
mean = (-51 -49 -90) / 3 = -63.333...
abs deviation so với median = [0,2,39] → MAD = 2
min = -90, max = -49
~~~

Gateway dùng **median** cho WCL. Mean/MAD/min/max để phân tích dữ liệu; hiện không
có bước dùng MAD để xóa mẫu hoặc tự thay median. RSSI thô vẫn được giữ.

Sau đó kiểm tra đủ node, clock_synced, cùng epoch_ms, và giao cửa sổ:

~~~text
overlap = max(0, min(các end) - max(các start))
~~~

Ngưỡng mặc định 600 ms cho window 700 ms. Cùng epoch ID nhưng cửa sổ quá lệch
vẫn không hợp lệ. Overlap là điều kiện chấp nhận; thống kê hiện lấy mẫu của cửa
sổ từng node, chưa cắt lại raw chỉ ở đoạn giao.

on_epoch_quality() lưu kết quả chất lượng cho cả epoch không định vị được.
Chỉ target có đủ 4 node và đạt điều kiện mới vào on_unified_epoch(). Hàm này
lưu evidence, lọc target theo cấu hình, chạy WCL, lưu vị trí rồi broadcast.

### 13.7. WCL biến RSSI thành tọa độ thế nào?

Bốn góc chuẩn hóa:

~~~text
ESP01=(0,0), ESP02=(1,0), ESP03=(0,1), ESP04=(1,1)
~~~

1. Lấy max RSSI là số ít âm nhất.
2. weight_i=10^((RSSI_i-max_RSSI)/10).
3. u=sum(weight_i*u_i)/sum(weight_i), v tương tự.
4. x=u*room_width, y=v*room_length.
5. zone_id() tìm ô lưới chứa tọa độ đó.

Ví dụ vector [-50,-60,-60,-70] có weights [1,0.1,0.1,0.01].
u=v=0.11/1.21≈0.0909. Phòng 8×12 m cho x≈0.727 m, y≈1.091 m:
kết quả lệch về ESP01 vì node đó nghe mạnh nhất. Nếu 4 RSSI bằng nhau, kết quả
là giữa phòng. Đây là ví dụ tính toán, không phải phép đo khoảng cách thật.

localize() tự nó chấp nhận vector chưa đủ node nếu gọi trực tiếp; chính
EpochReconstructor đảm bảo strict_4 trong đường chạy hệ thống. Không hiểu nhầm
rằng mọi lời gọi localize() đều tự kiểm tra 4 node.

### 13.8. Browser nhận gì?

Khi trang mở: init() đọc room, đọc trạng thái node, mở WebSocket theo session;
sau đó refreshNodes() dùng HTTP mỗi 1 giây. Vì vậy có hai dòng cập nhật:

- HTTP: thông tin phòng và online/offline của node.
- WebSocket: EPOCH_QUALITY và kết quả vị trí của session đang xem.

EPOCH_QUALITY chỉ đổi dòng trạng thái. Kết quả localization gọi renderResult()
để cập nhật tọa độ/RSSI và drawRoom() để vẽ SVG. Khi epoch lỗi, chấm có thể vẫn
ở **vị trí hợp lệ cuối cùng**; không được hiểu đó là vị trí mới của epoch lỗi.
Confidence là chỉ số heuristic, không phải xác suất đúng đã hiệu chuẩn.

## 14. Chức năng từng file

| File/thư mục | Chức năng | Chạy ở đâu? |
|---|---|---|
| firmware/platformio.ini | Chọn board/framework/toolchain và ArduinoJson/PubSubClient | Máy build/flash |
| firmware/src/config.h | Hằng số bộ nhớ/NTP/salt/GPIO, struct NodeConfig và mặc định | ESP32 |
| firmware/src/web_config.h | Khai báo hàm/biến dùng chung giữa hai file .cpp | ESP32, lúc compile |
| firmware/src/web_config.cpp | NVS, HTTP validation, API và trang cấu hình nhúng không đăng nhập | ESP32 + JS trên browser |
| firmware/src/main.cpp | Wi-Fi/SNTP, sniffer, queue, window, publisher, setup/loop | ESP32 |
| firmware/.gitignore | Bỏ qua file build theo cấu hình firmware | Git |
| firmware/.vscode/extensions.json | Gợi ý extension cho editor, không thuộc runtime | VS Code |
| gateway/config.py | Đọc settings, đường dẫn database, tính cấu hình grid | Laptop Python |
| gateway/models.py | Định dạng dữ liệu và quy tắc kiểm tra bằng Pydantic | Laptop Python |
| gateway/mqtt_ingest.py | Paho client, subscribe và tiếp nhận/validate MQTT | Laptop Python |
| gateway/epoch_reconstructor.py | Ghép chunks/epoch, thống kê, đồng bộ và timeout/dedup | Laptop Python |
| gateway/localization.py | Công thức WCL, tọa độ/zone/confidence và dữ liệu lưới | Laptop Python |
| gateway/database.py | Toàn bộ SQL mở/tạo/migrate/ghi/đọc/export | Laptop Python |
| gateway/main.py | Nối các thành phần, FastAPI, callbacks, HTTP/WebSocket | Laptop Python |
| gateway/static/index.html | HTML/CSS/JavaScript Dashboard, vẽ bản đồ SVG | Browser |
| gateway/requirements.txt | Dependencies chạy Gateway | pip |
| gateway/.env.example | Mẫu biến cấu hình, không tự là .env đang hoạt động | Người cấu hình |
| gateway/__init__.py | Đánh dấu regular package gateway, hiện chỉ docstring | Cơ chế import Python |
| scripts/mosquitto.conf | Listener broker nhận MQTT trên IPv4 | Mosquitto |
| scripts/start_mqtt.bat | Mở broker bằng đúng file cấu hình | Windows |
| scripts/start_gateway.bat | cd vào gateway rồi python main.py, dùng Python của terminal | Windows |
| database/rf_gateway.db | SQLite runtime: dữ liệu và cấu hình phòng đã lưu | SQLite |
| database/.gitkeep | Giữ thư mục database trống trong Git, không có code | Git |
| .gitignore | Ignore venv, .env, DB, raw export, build/cache | Git |
| .venv | Interpreter/thư viện Python cài cục bộ, có thể tạo lại | Laptop |
| .pio, __pycache__ nếu có | File build/bytecode tự sinh, không phải dữ liệu đo | Công cụ |
| README.md | Hướng dẫn chạy và giải thích source trong cùng tài liệu | Người đọc |

## 15. Từng hàm firmware/src/main.cpp

| Hàm | Ai gọi? | Làm gì và vì sao? |
|---|---|---|
| macEquals(a,b) | Callback lọc frame | So 6 byte MAC; dùng để nhận target, bỏ node MAC và nhận AP. |
| makeDeviceToken(mac) | appendSample() | SHA256(MAC bytes + salt), trả hex 64 ký tự. Làm ở publisher, không crypto trong callback. |
| timeSynced(tv) | SNTP | Lấy mốc monotonic, tính unixOffsetUs, bật clockSynced khi UTC hợp lệ; bảo vệ dữ liệu chia sẻ bằng captureMux. |
| promiscuousCallback(buf,type) | Wi-Fi driver | Chụp monotonic ngay đầu; kiểm tra loại/độ dài header, MAC/channel/RSSI; gate window; tạo RawRssiSample; đẩy queue không block, đầy thì tăng queueDrops. |
| header(d,w,index,count) | publishBatch() | Điền metadata chung vào JSON: session/node/epoch/window/sync/chunk/counters và tạo observations rỗng. |
| appendSample(d,s) | publishBatch() | Thêm đúng một mẫu raw vào JSON, tạo token, giữ nguyên RSSI và timestamps, ghi frame type/subtype/channel. |
| mqttEnsureConnected() | publisherTask() | Nếu STA có mạng và tới thời gian retry thì connect broker; thành công gửi NODE_READY v1. Chỉ publisher dùng MQTT client. |
| publishBatch(w,count) | publisherTask() | Đo measureJson để tính ranh giới chunks theo cả byte và số mẫu; serialize lại với chunk_count thật; publish tối đa 2 lần/chunk; cập nhật drops/fails/latency/log. Batch rỗng vẫn gửi heartbeat. |
| publisherTask(void*) | FreeRTOS | Loop riêng nhận queue, giữ batch duy nhất. Gặp mẫu epoch mới khi batch cũ chưa có marker thì loại batch cũ; marker aborted thì bỏ, marker thường thì publishBatch. |
| enterAP() | setup()/loop() | Đóng capture, tắt radio sniff khi cần, chuyển sang AP provisioning, phát RF-Setup-chipID và log địa chỉ web. |
| captureControl() | loop() khi STA đã bật sniff | Tính epoch hiện tại, đóng window hết hạn và đẩy marker; mở window mới trong đoạn sniff. Chỉ điều khiển cửa sổ, không tính thống kê. |
| fillStatus(d) | GET /api/status | Tạo JSON không secrets: mode/IP/channel/sync/MQTT, queue/counters/heap/latency/epoch. |
| setup() | Arduino, một lần sau reset | Load NVS, tạo queue/buffer, parse target, chuẩn bị Wi-Fi/MQTT, mở web và tạo publisher task trên core phù hợp. |
| loop() | Arduino, lặp liên tục | serviceWebConfig, kiểm tra BOOT giữ 3 giây, Wi-Fi reconnect/fallback, khởi động SNTP, bật promiscuous, gọi captureControl; mất STA thì gửi marker abort và dừng sniff. |

RawRssiSample chứa mẫu hoặc marker: marker=true là **tín hiệu kết thúc window**,
không phải một frame Wi-Fi. aborted=true là window bị ngắt, không đủ điều kiện
dùng như một window hoàn chỉnh. samples[] và chunkEnds[] chỉ publisher sở hữu.

queueDrops đếm mẫu không vào queue; batchDrops đếm mẫu bị loại ở publisher;
publishFails đếm lần gửi lỗi; markerDrops đếm marker không vào queue.
publishLatencyMs đo thời gian thực thi publishBatch, không phải độ trễ toàn tuyến
từ lúc nhận frame đến khi browser hiển thị.

## 16. Từng hàm web config trên ESP32

### 16.1. C++ trong web_config.cpp

| Hàm | Chức năng |
|---|---|
| toJson(d,c,secrets) | Chuyển NodeConfig sang JSON. secrets=true khi lưu NVS; false khi GET/export để không lộ password. |
| validMac(s) | Kiểm tra đúng 17 ký tự dạng XX:XX:XX:XX:XX:XX, hex hợp lệ, unicast và khác MAC toàn 0. |
| valid(c) | Kiểm tra toàn bộ cấu hình: node ID, SSID/password Wi-Fi, broker/port, session, target/mode, epoch/window, batch limits. Không kiểm chứng được node_id có trùng board khác trên mạng hay không. |
| apply(d,c) | Đọc các trường JSON có gửi, giữ giá trị cũ cho trường bỏ qua; kiểm tra kiểu, chuẩn hóa MAC in hoa rồi valid(c). Áp dụng vào bản candidate, không sửa cấu hình callback đang dùng. |
| requestAllowed() | Chỉ từ chối request khi đã hẹn reboot để tránh ghi tiếp giữa lúc sắp restart. Không kiểm tra đăng nhập hoặc gửi HTTP Basic challenge. |
| sendJson(d) | Serialize JSON trả HTTP 200, Content-Type JSON, Cache-Control no-store. |
| body(d) | Kiểm tra requestAllowed, Origin/Content-Type, body <=2048 ký tự và JSON object hợp lệ. Các route POST gọi trước khi xử lý. |
| loadConfig() | Đọc blob config trong namespace NVS rf-node, kiểm tra schema=1 và valid; hợp lệ mới thay nodeConfig mặc định. |
| factoryResetConfig() | Xóa namespace NVS rf-node; caller chịu trách nhiệm trả response/reboot. |
| startWebConfig() | Đăng ký các route, thu thập headers cần kiểm tra, bắt đầu WebServer cổng 80. |
| serviceWebConfig() | handleClient() xử lý HTTP; nếu đến rebootAt thì restart. Được gọi từ loop(). |

NodeConfig và các hằng số ở config.h không phải hàm. web_config.h chỉ khai báo
để compiler biết hàm/biến nằm ở file khác; extern không tạo thêm cấu hình thứ hai.

### 16.2. Các callback route nằm trong startWebConfig()

| Route | Hành động |
|---|---|
| GET / | Kiểm tra không chờ reboot rồi trả HTML PAGE nằm trong flash ESP32, không cần đăng nhập. |
| GET /api/config | requestAllowed rồi toJson(...,false), không trả mật khẩu Wi-Fi. |
| GET /api/status | requestAllowed rồi gọi fillStatus và sendJson. |
| POST /api/config | body → tạo candidate → apply/valid → serialize có secrets → ghi một blob NVS → trả success → hẹn reboot sau khoảng 1 giây. |
| POST /api/restart | body → trả success → hẹn reboot. |
| POST /api/factory-reset | body → bắt buộc confirm=factory-reset → xóa NVS → trả success → hẹn reboot. |

Đây là lambda không có tên hàm riêng. save không sửa nodeConfig runtime ngay:
reboot sẽ gọi loadConfig đọc bản mới, tránh trộn session/target cũ với mới.

### 16.3. JavaScript trong PAGE, chạy trên browser

| Hàm | Chức năng |
|---|---|
| call(path,data) | GET /api/path nếu không có data; có data thì POST JSON; kiểm tra HTTP thành công rồi đọc JSON. |
| init() | Lấy config không secrets, sinh input từ các trường, thêm ô Wi-Fi password rỗng và đọc Status ban đầu. Không có ô mật khẩu admin. |
| status() | GET status và in JSON/lỗi lên vùng kết quả. |
| save() | Lấy form, đổi trường number, bỏ qua ô password trống để giữ cũ; checkbox Open Wi-Fi thì gửi password rỗng; POST config. |
| action(a) | Hỏi confirm cho restart/factory-reset, POST confirm=a. |
| exportConfig() | GET config không secrets, tạo Blob và tải node-config.json, giải phóng object URL. |

Hai file cùng tên init() ở web node và Dashboard là hai trang riêng, không gọi
lẫn nhau. Browser tải trang từ ESP32 mới chạy các hàm của PAGE.

## 17. Từng class/hàm Gateway

### 17.1. config.py — cấu hình và đường dẫn

| Thành phần | Chức năng |
|---|---|
| PROJECT_ROOT | Thư mục cha của gateway, lấy từ __file__, không từ terminal cwd. |
| DATABASE_DIR | PROJECT_ROOT/database, nơi mặc định giữ SQLite. |
| resolve_database_path(path) | Chuẩn hóa tên/đường dẫn, map vị trí mặc định cũ về database/rf_gateway.db; tương đối đặt dưới database; giữ :memory: và absolute override tùy chỉnh. Không di chuyển/gộp dữ liệu. |
| NodePosition | Dataclass chứa node_id,x_m,y_m, không có hàm tự viết. Được build_settings tạo nhưng WCL hiện dùng bốn góc chuẩn hóa riêng. |
| GridConfig | Dataclass số cột/hàng và kích thước ô; không có hàm tự viết. |
| Settings | Dataclass các tham số MQTT/DB/room/target/timeouts, mặc định từ env. frozen=True để không sửa trực tiếp từng trường. |
| Settings.grid | Property gọi make_grid với kích thước hiện tại, trả GridConfig; không phải biến lưu grid một lần. |
| make_grid(...) | Chặn kích thước tối thiểu và dùng ceil(room/cell) để tính cột/hàng; ô cuối có thể nhỏ hơn. |
| build_settings(...) | Tạo Settings mới khi đổi phòng/grid, giữ MQTT/DB/target/timeouts của base và cập nhật các góc vật lý. |
| DEFAULT_SETTINGS | Cấu hình nền được tạo khi import module. |
| default_config_path() | Helper trả Path SQLite đã resolve; hiện đường runtime chính không gọi helper này, mà Database tự resolve đường dẫn. |

Dataclass tự sinh constructor, không cần người dùng viết __init__ cho từng kiểu.
Settings không tự reload .env khi sửa file trong lúc server đang chạy.

### 17.2. models.py — hợp đồng dữ liệu

| Class/hàm | Vai trò |
|---|---|
| StrictModel | BaseModel nền: strict=True, extra=forbid; không tự ép chuỗi thành số hoặc chấp nhận trường dư. |
| NodeReady | Kiểm tra NODE_READY v1: session, node, message_id, timestamp. |
| ObservationItem | Thống kê cho một target ở một node; dùng sau khi Gateway tính và cho tương thích v1. |
| EpochObservation | Report v1 có sẵn thống kê, chỉ dùng chẩn đoán trong strict sync hiện tại. |
| EpochObservation.validate_epoch_window() | Kiểm tra sniff_end_ms > sniff_start_ms; được reconstructor gọi. |
| RoomConfigRequest | Body API sửa kích thước phòng/lưới, các kích thước phải >0.1 và <=1000 m. |
| LocalizationResult | Kết quả gửi browser: track/session/epoch, x/y/zone/confidence, vector, count/drop/overlap. |
| EpochDeviceEvidence | Model mô tả evidence; hiện chưa được runtime instantiate, Database nhận các trường trực tiếp. Giữ như định nghĩa dữ liệu tham chiếu. |
| RawObservation | Kiểm tra từng mẫu v2: token, RSSI nguyên -127..0, channel 1..14, type/subtype và hai timestamp. |
| RawEpochObservation | Report raw v2: metadata window/chunk/counters và danh sách mẫu. |
| RawEpochObservation.validate_window() | Validator chạy sau field validation: index<count, window nằm trong epoch, sample nằm trong window; unsynced không được nhận là có Unix time. |

Các field không phải hàm riêng. model_validate()/model_dump()/model_dump_json()
là hàm của thư viện Pydantic được kế thừa, không được viết lại trong project.

### 17.3. mqtt_ingest.py — cửa nhận MQTT

| Hàm | Chức năng |
|---|---|
| MqttGateway.__init__(...) | Tạo Paho MQTT v3.1.1 client/callback API v2, gắn settings/reconstructor/callback node, cài auth broker nếu cấu hình. |
| MqttGateway.start() | connect_async và loop_start: chạy network loop trong thread nền. |
| MqttGateway.stop() | Dừng network loop, disconnect, đặt connected=false. |
| MqttGateway._on_connect(...) | Callback Paho sau connect: thành công thì subscribe observation và ready, QoS 0. |
| MqttGateway._on_disconnect(...) | Cập nhật trạng thái và log broker đã mất kết nối. |
| MqttGateway._on_message(...) | Giới hạn bytes, decode JSON, chọn v1/v2 model, đối chiếu topic với node_id; report thì on_node_seen + add, ready thì on_node_seen + on_node_ready. Payload sai bị log loại lỗi, không echo dữ liệu nhạy cảm. |

Dấu _ đầu tên hàm là quy ước nội bộ, không khiến hàm tự chạy. Paho gọi vì
__init__ đã gán chúng vào client.on_connect/on_disconnect/on_message.

### 17.4. epoch_reconstructor.py — ghép mảnh và thống kê

| Thành phần/hàm | Chức năng |
|---|---|
| EpochBucket | Dataclass giữ session/epoch, mốc monotonic bắt đầu chờ, chunks từng node và report legacy. |
| EpochReconstructor.__init__(...) | Lưu expected_nodes/timeouts/callbacks; tạo buckets, tombstone/floor, RLock, counters và last_quality. |
| EpochReconstructor.add(report) | Validate node/window; chặn late/duplicate/conflict; tạo bucket có giới hạn; kiểm tra metadata, giới hạn mẫu; lưu raw rồi giữ chunk. Đủ node/chunk thì _emit. |
| EpochReconstructor.flush_expired() | Duyệt bucket quá timeout và _emit, được expiry_loop gọi định kỳ. Timeout dựa monotonic của Gateway, chỉ để quyết định chờ bao lâu. |
| EpochReconstructor._emit(key,bucket) | Lấy bucket ra, đóng epoch, giới hạn tombstone RAM; tính thống kê theo target/node đủ chunks; kiểm tra sync/overlap/đủ node; lưu/phát quality; chỉ đưa target complete sang on_complete. |

Timeout dùng arrival để giới hạn thời gian chờ; việc xác định epoch đồng bộ
dùng capture timestamps/node metadata, không dùng arrival time để ghép đại.

### 17.5. localization.py — WCL

| Thành phần/hàm | Chức năng |
|---|---|
| LocalizationEstimate | Dataclass kết quả x/y, u/v chuẩn hóa, zone/confidence/nodes/weights. |
| WCLLocalizationEngine.__init__(settings) | Lưu settings và bốn góc chuẩn hóa ESP01–04. |
| WCLLocalizationEngine.localize(rssi_by_node) | Bỏ node/RSSI không hợp lệ, tính weights, centroid chuẩn hóa, nhân kích thước phòng, tìm zone và confidence. Không có dữ liệu hợp lệ thì None. |
| WCLLocalizationEngine.zone_id(x,y) | Đổi tọa độ ra hàng/cột lưới, chặn ở biên phòng, trả Z1/Z2/... theo hàng. |
| WCLLocalizationEngine.grid_payload() | Sinh danh sách ô có tọa độ và kích thước thực để browser vẽ, xử lý hàng/cột cuối nhỏ hơn. |
| WCLLocalizationEngine._confidence(valid,u,v) | Heuristic kết hợp tỷ lệ node và độ cân bằng weights; u/v hiện nhận vào nhưng chưa dùng trong công thức. Không phải sai số đo hoặc xác suất đúng. |

### 17.6. database.py — các thao tác SQLite

| Hàm | Chức năng |
|---|---|
| Database.__init__(path) | Resolve đường dẫn, tự tạo thư mục cha, mở SQLite cho nhiều thread với khóa riêng, row_factory trả row theo tên, rồi _init_schema. :memory: dùng SQLite RAM. |
| Database._init_schema() | CREATE TABLE/INDEX IF NOT EXISTS; kiểm tra PRAGMA và thêm cột thiếu cho node/room từ DB cũ, không xóa dữ liệu cũ. |
| Database.close() | Đóng connection dưới lock, gọi khi Gateway kết thúc hoặc kiểm tra tạm đã xong. |
| Database.store_raw_chunk(report) | Đối chiếu bản raw cùng khóa nếu có; khác nội dung thì lỗi; INSERT OR IGNORE bản giống để không nhân đôi. |
| Database.store_quality(quality) | Lưu kết quả đóng epoch một lần theo session/epoch, cũng là tombstone trên disk. |
| Database.epoch_closed(session,epoch) | Tra epoch_quality để biết epoch đã đóng, dùng chống replay kể cả restart Gateway. |
| Database.latest_quality(limit) | Lấy các epoch_quality mới nhất theo thứ tự lưu, parse JSON thành dict. |
| Database.export_raw(session_id) | Generator xuất JSONL theo session, từng trang 100 rows; chốt upper ID lúc bắt đầu để không đuổi theo dữ liệu mới vô hạn. |
| Database.upsert_node_ready(...) | Insert hoặc cập nhật node_status với session/thời gian ready và seen. |
| Database.upsert_node_seen(...) | Cập nhật lần nhận message gần nhất; node chưa tồn tại thì tạo record. |
| Database.get_node_status() | Đọc node_status lưu disk. Hiện API online/offline dùng bộ nhớ GatewayRuntime.node_status(), chưa gọi hàm DB này. |
| Database.save_room_config(...) | Lưu một cấu hình phòng/lưới với id=1, lần sau cập nhật record đó. |
| Database.load_room_config() | Đọc phòng/lưới; DB cũ chỉ có số hàng/cột thì suy kích thước cell để tương thích. |
| Database.upsert_epoch_device_evidence(...) | Lưu vector median/count của epoch/target trước lọc target định vị; chỉ các vector đã qua reconstructor mới đến đây. |
| Database.upsert_localization_update(**kwargs) | Lưu hoặc cập nhật kết quả WCL theo session/epoch/track, vector RSSI được serialize JSON. |
| Database.latest_localization(limit) | Đọc lịch sử mới nhất; parse vector và ghép quality để trả sync/count/drop/overlap cho UI lịch sử. |

upsert nghĩa là có khóa thì UPDATE, chưa có thì INSERT. self.lock giữ connection
khỏi hai thread truy cập đồng thời; with self.conn quản lý commit/rollback.

### 17.7. main.py — nối hệ thống và API

| Hàm | Chức năng |
|---|---|
| token_for_mac(mac_text,salt) | Chuyển MAC dạng chữ thành 6 byte rồi SHA256(bytes+salt), cùng thuật toán firmware để Gateway chọn đúng target. |
| WebSocketManager.__init__() | Tạo danh sách client→session, asyncio lock và chỗ lưu event loop. |
| WebSocketManager.add(websocket,session) | Accept socket, kiểm tra max clients, lưu client theo session hoặc đóng với code 1013. |
| WebSocketManager.remove(websocket) | Bỏ client khỏi danh sách khi đóng/lỗi. |
| WebSocketManager.broadcast(data) | Chỉ gửi cho client cùng session_id, loại các socket lỗi. |
| GatewayRuntime.__init__() | Mở/log DB, đọc room, tạo settings/localizer/manager/reconstructor/MQTT, gắn callbacks và tạo bộ nhớ last-seen cho 4 node. |
| GatewayRuntime.target_token | Property: ưu tiên TARGET_DEVICE_TOKEN; nếu trống thì tính từ TARGET_DEVICE_MAC+salt; không cấu hình thì None. |
| GatewayRuntime.set_room(...) | Tạo Settings mới, tạo lại localizer, lưu room xuống SQLite. Không flash hay đổi tọa độ trên ESP. |
| GatewayRuntime.on_node_seen(...) | Ghi thời gian nhận hiện tại vào RAM và node_status DB, nhớ session/message type để UI online. |
| GatewayRuntime.on_node_ready(ready) | Lưu record ready và log node bắt đầu xuất hiện. |
| GatewayRuntime.node_status() | Tính online/offline theo last_seen RAM và timeout 3500 ms mặc định. Sau restart cần message mới để online lại. |
| GatewayRuntime.should_localize(token,device_count) | LOCALIZE_TARGET_ONLY=false thì cho mọi token; nếu có target thì so token; không target thì chỉ tự chọn khi đúng một target hợp lệ. |
| GatewayRuntime.on_unified_epoch(...) | Nhận các vector complete, lưu evidence, lọc target, chạy WCL, tạo LocalizationResult, lưu SQLite, schedule broadcast và log vector. |
| GatewayRuntime.on_epoch_quality(quality) | Lưu quality và schedule broadcast EPOCH_QUALITY dù không có vị trí mới. |
| expiry_loop() | Vòng asyncio gọi flush_expired rồi sleep 0.1 giây; giữ timeout hoạt động cả khi MQTT im lặng. |
| lifespan(app) | Lifecycle FastAPI: nối event loop, start MQTT + expiry; khi dừng thì cancel expiry, stop MQTT và close DB. |
| dashboard() | GET / trả file static/index.html. |
| health() | GET /health trả broker/status/grid/counters/target_configured và database_path thực tế. |
| get_nodes() | GET /api/v1/nodes trả trạng thái node từ runtime. |
| epoch_quality(limit) | GET /api/v1/epochs/quality đọc quality mới nhất, giới hạn 1..200. |
| export_raw(session_id) | GET /api/v1/raw/export tạo StreamingResponse JSONL từ Database.export_raw, có header download. |
| get_room() | GET /api/v1/room trả kích thước, bốn góc vật lý, zones và số hàng/cột/kích thước ô cuối. |
| update_room(request) | POST /api/v1/room: request đã Pydantic validate, gọi set_room rồi trả room mới. |
| latest_localization(limit) | GET /api/v1/localization/latest đọc lịch sử, giới hạn 1..200. |
| localization_websocket(websocket,session_id) | Đăng ký socket theo session, gửi lịch sử cùng session trong 50 kết quả mới nhất toàn DB rồi giữ kết nối; đóng/lỗi thì remove. |

Khối if __name__ == "__main__" chạy khi python main.py: khởi động Uvicorn.
Khi dùng uvicorn main:app thì module được import, khối này không tự chạy.

## 18. Từng hàm Dashboard trong gateway/static/index.html

| Hàm/callback | Chức năng |
|---|---|
| $(id) | Arrow helper gọi document.getElementById(id), lấy phần tử UI. |
| apiJson(url,options) | Wrapper fetch, kiểm tra HTTP status, trả JSON hoặc ném lỗi. |
| loadRoom() | GET room, điền các input và gọi updateConfigText/drawRoom. |
| updateConfigText() | Hiển thị số cột/hàng, kích thước cell/phòng ở dòng mô tả. |
| applyRoom() | Đọc input thành number, kiểm tra >0 phía browser, POST room; server còn validate chặt hơn; cập nhật UI theo response. |
| nodeStatusColor(id) | Trả màu xám khi chưa biết, xanh online, đỏ offline. |
| renderNodeCards() | Vẽ 4 thẻ node với online/session/last message và đếm số online. |
| refreshNodes() | GET nodes, cập nhật nodeStates, vẽ cards và room; chạy mỗi giây. |
| drawRoom() | Tính tỉ lệ mét→pixel, vẽ SVG room/zones/node labels và vị trí lastResult. |
| renderResult(r) | Lưu lastResult, vẽ lại room, điền zone/x/y/nodes/confidence/epoch/RSSI/count/drops. |
| connect() | Đóng socket cũ, clear retry timer, mở WebSocket URL theo session của input, gắn callbacks. |
| ws.onopen | Hiển thị WebSocket connected. |
| ws.onclose | Báo mất kết nối, hẹn connect lại sau 1500 ms. |
| ws.onerror | Hiển thị lỗi WebSocket. |
| ws.onmessage | Parse JSON; quality thì cập nhật status/overlap, localization thì renderResult. |
| $('apply').onclick | Gắn nút Apply vào applyRoom. |
| $('session').onchange | Đổi session thì gọi connect để chuyển kênh nhận kết quả. |
| init() ở cuối script | Hàm async tự gọi: loadRoom → refreshNodes → connect → setInterval(refreshNodes,1000). |

Các callback map/filter trong hàm vẽ chỉ duyệt danh sách để tạo HTML/SVG, không
phải tác vụ định vị hoặc một pipeline riêng.

## 19. __init__.py có dùng không và database lưu những gì?

### 19.1. __init__.py

gateway/__init__.py hiện chỉ có docstring, **không có hàm và không chạy MQTT/WCL**.
Nó đánh dấu gateway là regular Python package. Khi import gateway, Python nạp
__init__.py. Bản Python mới còn hỗ trợ namespace package không cần file này,
nhưng giữ nó giúp ý định package rõ ràng.

Trong cách chạy hiện tại uvicorn main:app --app-dir gateway hoặc python main.py
tại gateway, module được nạp là main và các import nội bộ đang là from config
import..., nên __init__.py **không phải điểm khởi động và không được gọi trực
tiếp trong tuyến đó**. Giữ file này là hợp lý, không gây thêm task/tốn dữ liệu.

Không tự đổi lệnh thành uvicorn gateway.main:app hoặc python -m gateway.main:
việc có __init__.py không tự sửa các absolute import nội bộ thành package import.
Muốn hỗ trợ đồng thời các cách chạy đó cần refactor imports riêng.

### 19.2. Một file SQLite chứa nhiều bảng

| Bảng | Chứa gì? | Khi nào ghi? |
|---|---|---|
| raw_chunks | JSON raw nhận từ node, session/epoch/node/chunk và received_at | Chunk mới hợp lệ, trước thống kê |
| epoch_quality | Chất lượng, overlap, node_stats, diagnostics/drop và token statuses; tombstone epoch | Epoch đóng do đủ dữ liệu hoặc timeout |
| epoch_device_evidence | Vector median và sample count của target/epoch | Vector complete đến runtime, trước lọc target WCL |
| localization_updates | x/y/zone/confidence/nodes/vector theo track/session/epoch | WCL ra kết quả cho target được chọn |
| node_status | Session, ready/last-seen của node | MQTT ready/observation |
| room_config | Một cấu hình phòng và cell grid đang dùng | Người dùng Apply room/grid |

Không cần tạo riêng một DB cho raw và một DB cho WCL. Hai file cũ phát sinh do
thư mục chạy khác nhau, không phải thiết kế cần hai database.

Đường dẫn mới:

~~~text
C:\Users\Admin\Downloads\do an\
  database\
    rf_gateway.db
~~~

Chạy từ gốc project hay từ gateway đều chọn file mặc định này. Tên tương đối
buoi-do-002.db thành database/buoi-do-002.db. database/buoi-do-002.db cũng cho
cùng đường dẫn. Absolute override tùy chỉnh vẫn được tôn trọng khi đặt rõ ràng;
các đường dẫn mặc định cũ trong project được redirect sang vị trí mới.

database/.gitkeep không giữ dữ liệu; nó chỉ giữ thư mục trong Git. Các file .db
đã bị .gitignore bỏ qua, cần tự backup dữ liệu đo. Folder database tự tạo nếu
thiếu. Không có tự chuyển/gộp dữ liệu cũ khi startup.

### 19.3. Cách tự lần theo một mẫu khi đọc source

Bắt đầu từ tên hàm theo thứ tự sau, sẽ thấy “đầu vào → xử lý → đầu ra”:

~~~text
promiscuousCallback
 → xQueueSend
 → publisherTask / xQueueReceive
 → publishBatch / appendSample / header
 → Mosquitto
 → MqttGateway._on_message
 → EpochReconstructor.add
 → Database.store_raw_chunk
 → EpochReconstructor._emit
 → GatewayRuntime.on_epoch_quality
 → GatewayRuntime.on_unified_epoch
 → WCLLocalizationEngine.localize
 → Database.upsert_localization_update
 → WebSocketManager.broadcast
 → ws.onmessage / renderResult / drawRoom
~~~

Khi mất dữ liệu, kiểm tra từng ranh giới này: callback có sample? queue có drop?
MQTT đã tới laptop? raw_chunks đã có row? quality reject vì sao? WCL có vector?
WebSocket có đúng session? Cách đó giúp phân biệt lỗi thu RF, lỗi mạng, lỗi điều
kiện đồng bộ và lỗi hiển thị thay vì gom chung thành “không định vị được”.
