# YÊU CẦU REFACTOR ĐỒ ÁN RF LOCALIZATION — DÀNH CHO CODEX

> **Mục tiêu:** Giữ nguyên hệ thống đang chạy, nhưng chuyển ESP32 thành **node thu RSSI thô + truyền MQTT**, còn **Gateway xử lý RSSI, đồng bộ vector và định vị**. Bổ sung web cấu hình AP/STA và tách luồng thu–gửi bằng FreeRTOS Queue.
>
> **Quan trọng:** Hãy **đọc code hiện có trước khi sửa**, không tự viết lại toàn bộ. Chỉ sửa những phần cần thiết, giữ tương thích với dashboard/database ở mức hợp lý. **Giữ nguyên cơ chế NTP/SNTP hiện tại** theo yêu cầu; không tự chuyển sang HTTP time sync hoặc NTP server nội bộ.

## 0. Phạm vi và nguyên tắc

- Dự án có 4 ESP32 (`ESP01`–`ESP04`), Wi-Fi STA + promiscuous sniffing, MQTT Mosquitto, Python Gateway, SQLite, WCL và dashboard.
- ESP32 dùng **Wi-Fi 2.4 GHz trên kênh của mạng STA**; cấu hình mạng hiện tại do người dùng nhập qua web.
- ESP32 **không** tính `mean`, `median`, `MAD`, lọc nhiễu, WCL, fingerprinting hoặc tạo vector RSSI đại diện. Chỉ lọc khung cơ bản, xác định target MAC nếu được cấu hình, gắn timestamp/epoch và **đóng gói các mẫu RSSI nguyên bản** để gửi.
- Gateway mới thực hiện kiểm tra chất lượng, nhóm dữ liệu, tính các thống kê RSSI, tạo vector cùng epoch và chạy WCL.
- **Không chuyển sang ESP-NOW**, không thay MQTT bằng giao thức khác, không thay thuật toán WCL trong đợt này.
- **Không ép các tác vụ Wi-Fi hệ thống sang core khác.** ESP32 hai nhân dùng **Core 0 / Core 1**; callback promiscuous do Wi-Fi driver gọi trong ngữ cảnh của driver. Pin task do ứng dụng tạo khi cần.
- Vừa sniff vừa MQTT publish là song song về xử lý phần mềm; **không cam kết thu 100% frame khi Wi-Fi radio đang truyền**. Đo số mẫu và queue drops.
- Không log hoặc trả về mật khẩu Wi-Fi, mật khẩu web config, token bí mật; tránh hardcode thông tin nhạy cảm.

## 1. Khảo sát code trước khi sửa

Đọc các file tương ứng (tên/đường dẫn có thể khác theo bản repository):
- Firmware: `esp32_firmware/src/main.cpp`, `esp32_firmware/src/config.h`, `platformio.ini`.
- Gateway: `gateway/models.py`, `mqtt_ingest.py`, `epoch_reconstructor.py`, `localization.py`, `database.py`, `main.py`, `config.py`, dashboard và các test.
- Ghi lại sơ đồ task/callback, cấu trúc JSON hiện tại, cách sinh `epoch_id`, cách lấy thời gian NTP, giới hạn buffer MQTT và cách khởi tạo web server/Wi-Fi nếu đã có.
- Xác nhận **biến thể ESP32 và số core thực tế**; nếu board là ESP32 đơn nhân thì không pin hai task vào hai core.
- Trước khi sửa, bảo đảm có baseline build/test và giữ backup các cấu hình đang chạy.

## 2. Web cấu hình ESP32: AP khi cần, STA vẫn truy cập được

### 2.1 Chế độ vận hành

1. **Chưa có cấu hình Wi-Fi hợp lệ / kết nối STA thất bại nhiều lần / người dùng nhấn nút reset cấu hình:** vào **AP provisioning mode**, ESP32 phát SSID cấu hình riêng và chạy web server (địa chỉ AP hiển thị trên Serial, ví dụ `192.168.4.1` nếu cấu hình như vậy).
2. **STA kết nối thành công:** vẫn duy trì **web server trên IP STA** để cấu hình khi điện thoại/laptop cùng LAN; không bắt buộc quay về AP.
3. **Không mặc định bật AP+STA vĩnh viễn.** Chỉ thêm nếu thực sự cần; AP+STA có giới hạn một radio và phải dùng cùng channel với STA.
4. Khi thay SSID/password hoặc cấu hình yêu cầu restart: **save vào NVS → trả response thành công → khởi động lại hoặc reconnect có kiểm soát**. Nếu không kết nối được, fallback về AP.
5. Web config không được block hoặc làm treo sniff/MQTT. Có endpoint trạng thái để kiểm tra hoạt động.

### 2.2 Các trường cấu hình

| Trường | Quy tắc |
|---|---|
| `node_id` | `ESP01`…`ESP04`, duy nhất trong mạng |
| `wifi_ssid`, `wifi_password` | Lưu NVS; password không trả ngược qua API |
| `mqtt_host`, `mqtt_port` | Địa chỉ broker laptop, ví dụ `192.168.x.x:1883` |
| `session_id` | Cùng giá trị trên cả 4 node trong một phiên đo |
| `target_mac` | MAC nguồn cần theo dõi; có thể cho phép chế độ `all` để chẩn đoán |
| `capture_mode` | `target_only` hoặc `all` (cảnh báo lưu lượng cao) |
| `epoch_ms` | Mặc định `1000`; phải đồng nhất giữa 4 node |
| `sniff_window_ms` | Có thể giữ `700` nếu firmware cũ dùng; nếu thu liên tục thì ghi rõ cách phân epoch |
| `mqtt_batch_max_samples` | Giới hạn số mẫu trong một bản tin/chunk |
| `mqtt_batch_max_bytes` | Giới hạn kích thước JSON phù hợp bộ nhớ/broker |
| `web_admin_password` | Đặt lúc provisioning; bắt buộc xác thực khi vào web config qua STA |

- Có validation cho MAC (`XX:XX:XX:XX:XX:XX`), `session_id`, port, epoch, kích thước batch, SSID.
- Cấu hình lưu bằng **NVS/Preferences** với version/schema và giá trị mặc định an toàn; không làm mất cấu hình khi reset.
- Nên hỗ trợ **Export config không kèm secrets**, trang `Status`, `Save`, `Restart`, `Factory reset` có xác nhận.
- Khi thay `session_id`, không được trộn dữ liệu phiên cũ và mới; làm sạch batch đang mở và khởi tạo phiên mới rõ ràng.
- Khi thay `target_mac`, reset trạng thái target/batch có kiểm soát, tránh trộn hai target trong một epoch.
- Ghi nhận rằng `target_mac` có thể là MAC ngẫu nhiên của điện thoại; không mặc định coi một MAC là danh tính vật lý vĩnh viễn.

### 2.3 API tối thiểu

- `GET /` — giao diện cấu hình.
- `GET /api/status` — node ID, IP, mode AP/STA, Wi-Fi channel, MQTT connected, NTP status, queue depth, drop count, current session/epoch (không lộ secrets).
- `GET /api/config` — các trường không nhạy cảm; password phải bị ẩn.
- `POST /api/config` — validate, lưu NVS, trả kết quả; có xác thực.
- `POST /api/restart` — có xác thực.
- `POST /api/factory-reset` — có xác thực + xác nhận rõ ràng.

> Chọn thư viện web server phù hợp framework đang dùng; không thêm thư viện nặng nếu framework đã có API tương đương. Với HTTP trên LAN, dùng xác thực và hạn chế truy cập mạng đáng tin; không tuyên bố HTTP thường bảo mật tuyệt đối.

## 3. Pipeline ESP32: chỉ thu mẫu thô, không tính thống kê

### 3.1 Callback Wi-Fi

Trong promiscuous callback:
1. Kiểm tra frame hợp lệ và độ dài trước khi đọc header.
2. Lấy `source/transmitter MAC` đúng theo loại frame và các bit địa chỉ 802.11; **không mặc định `addr2` luôn là source của mọi frame**. Xác định rõ MAC được dùng để theo dõi và phân biệt traffic từ AP.
3. Lấy **RSSI nguyên gốc** từ `rx_ctrl.rssi` (dBm), `channel`, `frame_type/subtype`.
4. Lấy timestamp **ngay khi nhận callback** bằng monotonic clock, ví dụ `esp_timer_get_time()`. Có thể lưu cả timestamp UTC đã hiệu chỉnh nếu sẵn có; tuyệt đối phân biệt monotonic microseconds với Unix milliseconds.
5. Lọc MAC whitelist/target nếu bật `target_only`; trong chế độ `all` cho phép capture các MAC hợp lệ theo quy tắc đã định.
6. `xQueueSend(..., 0)` không block; nếu đầy, tăng `queue_drop_count`.
7. **Không serialize JSON, không MQTT publish, không mean/median/MAD, không cấp phát động lớn** trong callback.

Ví dụ struct khái niệm (Codex điều chỉnh cho code thực tế):

```cpp
struct RawRssiSample {
    uint8_t mac[6];
    int8_t rssi_dbm;
    uint8_t channel;
    uint8_t frame_type;
    uint8_t frame_subtype;
    int64_t capture_mono_us;
    uint64_t epoch_id;
};
```

`epoch_id` phải dựa trên đồng hồ đã đồng bộ và **thời điểm bắt được frame**, không dựa trên lúc MQTT task đọc queue.

### 3.2 Phân chia Core / FreeRTOS

- Giữ Wi-Fi driver/callback trên core mà ESP-IDF quản lý.
- Tạo **Publisher Task pinned Core 1** (nếu đúng board ESP32 dual-core), nhận mẫu qua `xQueueReceive()`.
- Nếu cần một **Capture Control Task** để điều khiển cửa sổ thu, có thể pin Core 0 ở mức ưu tiên thấp hơn Wi-Fi driver; không chạy xử lý nặng trên core này.
- Nên tách `Queue<RawRssiSample>` và batch/epoch buffer. Chỉ một task sở hữu/ghi batch tại một thời điểm để tránh race.
- Dùng mutex/event group cho thay cấu hình runtime, không cho web config sửa trực tiếp struct đang được callback sử dụng.
- Task MQTT cần reconnect, retry có giới hạn, theo dõi trạng thái broker và tránh blocking lâu làm queue đầy.
- Đặt `queue_capacity`, `max_samples_per_epoch`, `max_json_bytes` có cấu hình/hằng số rõ ràng, kèm log thống kê bộ nhớ.

### 3.3 Thu song song với MQTT

- **Không chủ động tắt promiscuous chỉ để publish MQTT**, trừ khi bắt buộc vì giới hạn driver và phải ghi log rõ.
- Task Publisher có thể publish epoch N trong lúc callback tiếp tục thu epoch N+1.
- **Không tính median/mean/MAD trên ESP32.** Batch chỉ là tập các bản ghi thô.
- Nếu vượt kích thước JSON: **chia chunk**; mỗi chunk giữ `session_id`, `node_id`, `epoch_id`, `chunk_index`, `chunk_count` hoặc cơ chế `final_chunk`.
- Có chính sách khi MQTT chậm/queue đầy: đếm và báo `queue_drop_count`, `batch_drop_count`, `mqtt_publish_fail_count`; không âm thầm bỏ mẫu.
- Chú ý: 2 core giúp chạy task song song nhưng **không có 2 radio Wi-Fi**. Khi STA truyền MQTT, có thể bỏ lỡ frame; cần đo thực nghiệm.

## 4. NTP/SNTP và epoch: GIỮ NHƯ CŨ

**Yêu cầu bắt buộc của người dùng:** giữ cơ chế NTP/SNTP đang dùng trong firmware; **không tự ý thay** bằng API `/api/v1/time`, đồng bộ HTTP hoặc dựng NTP server nội bộ trong lần refactor này.

- Kiểm tra logic `clockSynced`: chỉ báo đồng bộ khi NTP thực sự thành công; không gắn cờ synced giả khi fallback `millis()`.
- Giữ cấu hình NTP server/logic hiện hành; nếu không có Internet, **hiển thị trạng thái `unsynced` rõ ràng** và không coi `millis()` độc lập của từng node là thời gian chung.
- `epoch_id` phải cùng quy ước giữa 4 ESP, dựa trên Unix time sau đồng bộ NTP.
- Lưu thời điểm bắt đầu/kết thúc thực tế của từng batch/epoch và mức độ tin cậy đồng hồ (nếu đo được).
- Nếu NTP chưa thành công, chọn chính sách an toàn: không tạo vector đồng bộ chuẩn; vẫn có thể ghi raw diagnostics với cờ `clock_synced=false`.
- Không tự thay đổi NTP sang phương án khác khi chưa được yêu cầu.

## 5. MQTT schema: raw RSSI observations

**Mục tiêu:** ESP32 gửi đúng các giá trị RSSI đã thu, không tính trung bình/trung vị trước.

Topic có thể giữ `rf/v1/nodes/{node_id}/observation` để giảm thay đổi, nhưng **schema đã thay đổi**; nên bổ sung `schema_version=2` và cập nhật Gateway. Cân nhắc topic `/raw` nếu muốn giữ consumer cũ; chọn một phương án nhất quán.

Payload mẫu (minh họa, timestamp chỉ có ý nghĩa nếu được đồng bộ/ánh xạ đúng):

```json
{
  "schema_version": 2,
  "session_id": "session-dev-001",
  "node_id": "ESP01",
  "epoch_id": 1790196386,
  "clock_synced": true,
  "sniff_start_unix_ms": 1790196386000,
  "sniff_end_unix_ms": 1790196386700,
  "chunk_index": 0,
  "chunk_count": 1,
  "queue_drop_count": 0,
  "observations": [
    {
      "device_token": "example-target-token",
      "rssi_dbm": -51,
      "channel": 6,
      "frame_type": "mgmt",
      "capture_unix_ms": 1790196386120
    },
    {
      "device_token": "example-target-token",
      "rssi_dbm": -49,
      "channel": 6,
      "frame_type": "mgmt",
      "capture_unix_ms": 1790196386280
    }
  ]
}
```

- Nếu dự án đang dùng **device_token từ MAC + salt**, giữ cùng cơ chế giữa các node; đừng đưa raw MAC vào MQTT nếu không cần.
- Nếu timestamp chính xác chỉ có dạng `capture_mono_us`, gửi **mốc ánh xạ** UTC↔monotonic cho batch để Gateway suy ra thời gian; không gắn nhãn Unix sai.
- Một epoch **không có target** vẫn gửi heartbeat/empty batch (`observations: []`) với trạng thái node, giúp Gateway phân biệt không thấy frame với node offline.
- MQTT có thể đến không theo thứ tự. Gateway **không dựa vào arrival time** để khẳng định bốn node đã đo cùng lúc.
- Nếu dùng QoS 1, Gateway phải chống duplicate; QoS 0 cũng có thể mất message. Giữ mức QoS hiện hành ban đầu nếu đang ổn định.
- Giới hạn kích thước message, tránh heap fragmentation, không để payload quá lớn làm treo ESP32.

## 6. Gateway: toàn bộ xử lý RSSI chuyển về đây

### 6.1 MQTT ingest / schema

- Chấp nhận `schema_version=2` raw observations; nếu có thể, giữ backward compatibility với v1 hoặc nêu rõ breaking change.
- Validate node ID, session ID, epoch ID, timestamp, chunk index/count, RSSI hợp lệ, token, channel.
- Chống trùng chunk theo `(session_id, epoch_id, node_id, chunk_index)`.
- Ghép đủ chunks của một node trước khi tính thống kê; nếu thiếu chunk, đánh dấu incomplete.
- Không trộn session khác, token khác, epoch khác.
- Lưu **raw observations** (hoặc JSONL/CSV raw) trước xử lý để phục vụ train AI và debug.

### 6.2 Tính thống kê RSSI tại Gateway

Cho từng `(session_id, epoch_id, device_token, node_id)`:
- `packet_count = số mẫu hợp lệ`
- `median_rssi_dbm = median(raw_rssi)`
- `mean_rssi_dbm = mean(raw_rssi)`
- `mad_rssi_db = median(abs(raw_rssi - median_rssi))`
- `min_rssi_dbm`, `max_rssi_dbm`
- Có thể thêm IQR/EMA sau này nhưng **không bắt buộc trong đợt này**.
- Chỉ dùng **median** làm RSSI đại diện cho WCL trong giai đoạn hiện tại.
- Ghi nhận `queue_drop_count`, `clock_synced`, thời gian bắt đầu/kết thúc, chất lượng dữ liệu.

### 6.3 Ghép vector đồng bộ

- Group theo `(session_id, epoch_id, device_token)`.
- Chỉ tạo vector **đủ 4 node** ở chế độ `strict_4`; nếu thiếu node, ghi trạng thái `incomplete` và **không giả lập RSSI còn thiếu**.
- Đối chiếu cửa sổ thời gian thực tế của từng node, không chỉ đối chiếu `epoch_id`. Có thể thử ngưỡng overlap ≥600 ms cho cửa sổ 700 ms, nhưng phải cấu hình được.
- Đồng hồ phải có trạng thái synced; nếu không chắc, đánh dấu vector không đạt điều kiện đồng bộ.
- Tính overlap: `max(0, min(sniff_end) - max(sniff_start))`.
- Có timeout nhận chunk/node; khi epoch đã đóng, bản tin trễ không được làm Gateway phát lại vị trí cũ.
- Phân biệt trạng thái `complete`, `missing_node`, `missing_chunk`, `unsynced`, `low_overlap`, `late`, `duplicate`, `empty_target`.
- Khi có vector hợp lệ: `R_epoch = [median_ESP01, median_ESP02, median_ESP03, median_ESP04]` → chuyển sang WCL hiện tại.
- Giữ database/dashboard hoạt động; hiển thị `nodes_used`, epoch, trạng thái đồng bộ, số mẫu/node và số frame bị drop nếu khả thi.

## 7. Kiểm thử bắt buộc

### 7.1 Firmware/web config
- Lần đầu boot → AP → nhập Wi-Fi/MQTT/Node/Session/Target → Save → reboot → STA.
- Trong STA, điện thoại cùng LAN mở IP ESP32 và sửa `session_id`/target MAC được.
- Thay Wi-Fi sai → fallback AP; NVS vẫn an toàn.
- Web không trả mật khẩu qua API, có xác thực, input validation.
- Bốn node dùng `node_id` riêng, `session_id` giống nhau.

### 7.2 Thu–gửi song song
- Trong lúc MQTT publish, promiscuous callback vẫn tiếp tục được gọi (đo log/counter).
- So sánh số frame/giây khi chỉ sniff và khi sniff + MQTT trong điều kiện tương đương.
- Stress test queue/buffer; khi quá tải có counter, không treo, không reset.
- Kiểm tra raw RSSI nhận ở Gateway khớp từng giá trị đã publish, không bị tính median/round/truncate sai.
- Đo độ trễ publish, queue depth cao nhất, free heap.

### 7.3 Đồng bộ vector
- 4 node cùng target, epoch và cửa sổ thời gian → vector hợp lệ.
- MQTT đến đảo thứ tự vẫn ghép đúng.
- Mất 1 node → incomplete, không phát WCL trong strict_4.
- Thiếu 1 chunk → incomplete.
- Trùng chunk / bản tin đến trễ → không double count, không phát lại kết quả.
- 1 node chưa sync NTP → reject vector đồng bộ.
- 2 session / 2 target / 2 epoch xen kẽ → không trộn.
- Khi target im lặng hoặc không phát frame → không tạo vị trí giả.

### 7.4 Hệ thống end-to-end
- Giữ luồng `ESP32 → MQTT → Gateway → WCL → SQLite → WebSocket Dashboard`.
- Có log một vector đầy đủ gồm 4 median RSSI cùng epoch.
- Có file raw dataset xuất được cho buổi đo thực tế, kèm vị trí ground truth nếu người dùng cung cấp.
- Không có secrets trong log và commit.

## 8. Thứ tự triển khai đề nghị cho Codex

1. **Inspect + lập kế hoạch:** đọc code, ghi file sẽ sửa và các breaking changes.
2. **Firmware config:** NVS + web AP provisioning + web STA + validation/auth.
3. **Firmware pipeline:** callback lấy raw RSSI/timestamp → FreeRTOS Queue → MQTT publisher Core 1; bỏ thống kê RSSI trên node.
4. **MQTT schema v2:** raw observations, chunking, counters, empty heartbeat.
5. **Gateway ingest + raw storage:** parse v2, chống trùng, lưu raw.
6. **Gateway stats + epoch reconstructor:** median/mean/MAD, strict_4, timestamp overlap, timeout.
7. **Integration:** WCL, SQLite, dashboard, kiểm thử và hướng dẫn flash/chạy.
8. **Không triển khai chức năng ngoài phạm vi** như AI fingerprinting, MAC de-randomization, ESP-NOW, OTA, đổi NTP hoặc thay kiến trúc WCL.

## 9. Tiêu chí nghiệm thu (Definition of Done)

- [ ] Có web config trong AP và STA, lưu NVS, có fallback AP, xác thực và validation.
- [ ] Không cần sửa/flash lại code chỉ để đổi Wi-Fi, MQTT host, target MAC, session ID.
- [ ] NTP/SNTP giữ nguyên cơ chế hiện hành, không báo synced giả.
- [ ] Callback thu RSSI thô, timestamp và đẩy FreeRTOS Queue không block.
- [ ] MQTT publisher là task riêng, pin Core 1 trên ESP32 dual-core.
- [ ] ESP32 không còn tính median/mean/MAD.
- [ ] MQTT payload chứa **mọi mẫu RSSI thu được trong giới hạn buffer**, có chunking/counters rõ ràng.
- [ ] Gateway lưu raw RSSI, tính median/mean/MAD và ghép vector 4 node cùng epoch/time window.
- [ ] Gateway không dùng RSSI giả, không ghép lệch epoch, không double count chunk.
- [ ] WCL + database + dashboard vẫn chạy với vector hợp lệ.
- [ ] Có unit/integration tests và hướng dẫn build/flash/config/run.
- [ ] Có báo cáo kết quả thử nghiệm thực tế, nêu giới hạn một radio Wi-Fi và tỷ lệ mất mẫu nếu có.

## 10. Yêu cầu cách làm việc với Codex

- Trước tiên **tóm tắt kiến trúc hiện tại dựa trên code**, rồi nêu danh sách file sẽ sửa và kế hoạch theo từng commit/nhóm thay đổi.
- Chỉ bắt đầu code sau khi xác định được schema hiện tại, cách xử lý NTP và API MQTT đang dùng.
- **Ưu tiên minimal diff**, tái sử dụng queue, Wi-Fi, MQTT, database và web server hiện có nếu phù hợp.
- Mỗi giai đoạn phải build/test; nếu môi trường không có board thật, ghi rõ phần nào chỉ được kiểm tra tĩnh.
- Đưa ví dụ payload JSON thật, ví dụ log của 4 node và lệnh kiểm thử.
- Không tự tuyên bố đã đo trên phần cứng nếu chưa có log thực tế.
- **Không tự thay đổi những yêu cầu đã chốt**: NTP giữ như cũ; ESP32 gửi raw; Gateway xử lý thống kê; Wi-Fi STA + promiscuous; MQTT; web AP/STA.
