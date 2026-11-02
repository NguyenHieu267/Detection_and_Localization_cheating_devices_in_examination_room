# RF Localization — hướng dẫn chạy và đo thực tế

4 ESP32 thu RSSI thô và gửi MQTT về laptop. Gateway Python lưu SQLite, tính
thống kê RSSI, ghép vector 4 node cùng session/epoch/target rồi chạy WCL và hiển
thị Dashboard. README này gom cách chạy đã thực hiện trên Windows, các lỗi đã
gặp và quy trình thu dữ liệu thực tế.

## 1. Chạy lại nhanh khi đã cấu hình ESP32

Mở VS Code → Terminal → New Terminal. Dùng **hai terminal riêng**, giữ cả hai chạy.

**Terminal 1 — Mosquitto:**

~~~powershell
cd "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid"
& "C:\Program Files\mosquitto\mosquitto.exe" -c ".\scripts\mosquitto.conf" -v
~~~

Nếu cổng bị chiếm hoặc broker chỉ nghe localhost, xử lý theo mục 5.

**Terminal 2 — Gateway:**

~~~powershell
cd "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid"

$env:MQTT_HOST = "127.0.0.1"
$env:MQTT_PORT = "1883"
$env:TARGET_DEVICE_MAC = "66:C2:DE:00:BE:87"
$env:TARGET_DEVICE_TOKEN = ""
$env:DEVICE_TOKEN_SALT = "rf-mvp-shared-salt-change-me"
$env:LOCALIZE_TARGET_ONLY = "true"
$env:MIN_OVERLAP_MS = "600"
$env:EPOCH_TIMEOUT_MS = "1800"
$env:DATABASE_PATH = "$PWD\rf_gateway.db"

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
cd "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid"
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
Get-Item -Force "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid\.venv"
Remove-Item -LiteralPath "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid\.venv" -Recurse -Force
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
cd "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid"

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
| web_admin_password | Tự đặt, ít nhất 8 ký tự |

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

Đăng nhập user **admin**, password web đã tự đặt. README không lưu mật khẩu.
GET /api/config và Export cũng không trả password.

Nếu kết nối STA thất bại, sau ba đợt chờ khoảng 15 giây sẽ fallback AP, giữ NVS.
Nếu quên admin/cấu hình, giữ BOOT/GPIO0 ít nhất 3 giây **sau khi chương trình đã
boot** để factory reset. Không giữ BOOT ngay lúc cấp nguồn nếu muốn chạy bình
thường. Factory reset xóa cấu hình; restart thường giữ NVS. Đổi session/target
qua web sẽ save rồi reboot, không trộn batch cũ sang cấu hình mới.

AP bootstrap ban đầu mở khi chưa đặt admin nên provision trong môi trường kiểm
soát. Sau đó AP fallback và STA đều cần auth. HTTP/NVS mặc định không mã hóa
secrets, dùng trong mạng tin cậy.

## 8. Kiểm tra node và chạy Dashboard

Mở http://IP-NODE/api/status, đăng nhập admin. Cả 4 node phải có:

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

Lệnh mục 1 dùng rf_gateway.db tại gốc project. Có thể đồng thời có
gateway/rf_gateway.db từ cách chạy cũ. **Không coi chúng là bản sao hoặc tự xóa.**
Muốn dùng lịch sử trong gateway thì đặt trước khi chạy:

~~~powershell
$env:DATABASE_PATH = "$PWD\gateway\rf_gateway.db"
~~~

Không tự hợp nhất hai database. Ghi rõ file nào dùng cho buổi đo.

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
cd "C:\Users\Admin\Downloads\do an\rf_localization_mvp_node_grid"

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
rf_localization_mvp_node_grid/
  README.md
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