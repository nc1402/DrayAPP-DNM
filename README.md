# DrayTek Network Manager — v0.1.0 (Stage 3)

Mã nguồn Android Kotlin/Jetpack Compose và backend USP 1.3 kế thừa từ `TR 369.zip` (file `usp_web1.py` cùng hai module Protobuf đã biên dịch). Chỉ sử dụng API đọc thiết bị và quản lý danh sách Agent. Không có thao tác SET, OPERATE, reboot hay thay đổi Wi-Fi trong Android v0.1.

## 1. Build APK qua GitHub Actions (khuyên dùng)
1. Giải nén ZIP, đưa **toàn bộ nội dung thư mục DNM_Android_v0.1**, kể cả `.github`, lên một repository GitHub mới (khuyến nghị private vì có mã nguồn/schema DrayTek).
2. Vào Actions → Build DNM Android APK → Run workflow. Workflow tự chạy khi push main/master. Nếu Actions bị tắt, bật Actions trước.
3. Khi build thành công, mở run → Artifacts → `DNM-Android-v0.1-debug` để lấy `app-debug.apk`. Giải nén artifact, cài APK trên điện thoại. APK debug dùng thử nghiệm, chưa ký phát hành.
4. Mặc định app mở Demo offline (3 thiết bị minh họa, không cần PC, broker hoặc thiết bị thật).

Không có Android SDK/Gradle trong môi trường tạo gói này nên APK chưa được build hay chạy trên emulator. Workflow được cung cấp để thực hiện build; trạng thái build cần được xác nhận trên GitHub. Debug key của runner có thể thay đổi giữa các lần build; nếu Android báo xung đột chữ ký, gỡ bản debug cũ hoặc thiết lập debug/release keystore cố định trước khi phân phối bản cập nhật.

## 2. Build Android Studio
- Android Studio hỗ trợ AGP 8.7.3, JDK 17, Android SDK Platform 35, Gradle 8.9. minSdk 26 (Android 8), targetSdk 35.
- Mở thư mục chứa `settings.gradle.kts`. Đồng bộ Gradle; nếu IDE yêu cầu Gradle distribution, dùng 8.9, JDK 17. Không có Gradle wrapper binary trong gói.
- Hoặc dùng Gradle 8.9 đã cài: `gradle :app:assembleDebug`. APK ở `app/build/outputs/apk/debug/app-debug.apk`.

## 3. Demo thuần Android
- Tổng quan, trạng thái các thiết bị; danh sách tìm theo model / Endpoint ID, lọc AP/Router/offline.
- Chi tiết thông tin system / CPU/RAM/firmware/uptime; đọc các nhóm WAN/LAN/Wireless khi được hỗ trợ. Demo dùng snapshot từ API backend, model demo được đặt theo mockup đã duyệt; số liệu không phải thiết bị thật. Không tạo lưu lượng/CPU giả cho thiết bị thật.
- Clients: đọc theo từng Agent, không gộp các client trùng lặp.
- Cấu hình: chọn nhiều AP online, nhập SSID và xem trước; **không gửi SET**.
- Thêm: kết nối Controller, thêm Agent, TR-369 Explorer tìm schema và GET. Schema offline là file AP 905. Path chưa có snapshot trả lỗi rõ ràng.
- Material 3 sáng/tối theo thiết bị. Tiếng Việt; chưa có bản dịch EN đầy đủ.

## 4. Kiểm thử Android với Controller Demo chạy trên PC
Bên trong `backend`, dùng Python đã cài có sẵn trên PC. Backend có chế độ Demo chạy được ngay cả khi không có paho/protobuf.

`python usp_web1.py --demo --host 127.0.0.1 --port 8080 --no-browser`

Nếu dùng emulator Android Studio: chạy với `--host 0.0.0.0` và `--password` theo hướng dẫn LAN dưới đây hoặc bind IP LAN; emulator có thể kết nối 10.0.2.2 khi server lắng nghe đúng interface và host guard cho phép. Ưu tiên thiết bị Android thật cùng LAN:

1. Xác định IPv4 PC bằng `ipconfig`. Chạy `start_lan_demo.bat` (hoặc ví dụ `python usp_web1.py --demo --host 192.168.9.100 --port 8080 --password MAT_KHAU_DEMO --no-browser`, đổi IP/mật khẩu cho đúng). Chỉ dùng mật khẩu test tạm thời, chữ số/chữ cái, không dùng thông tin thật.
2. Cho phép inbound TCP 8080 từ LAN private cần thiết trong Windows Firewall, không port-forward WAN. Không dùng mạng Wi-Fi công cộng.
3. Android cùng LAN → tab Thêm → URL `http://192.168.9.100:8080` (thay IP) → mật khẩu web demo → Kết nối Controller. App đọc `/api/state`, `/api/devices`, `/api/pages` và gửi header Basic + `X-USP-Web: 1` khi cần.
4. Quét lại thiết bị, mở từng device, thử GET hoặc schema search. Nếu kết nối thành công nhưng danh sách ban đầu chưa quét, nhấn nút quét. Demo trên PC tự sinh model router; demo offline trong app có AP minh họa.

## 5. Dùng backend đã chạy tốt của bạn (lab nội bộ)
Khuyến nghị giữ nguyên môi trường Python và MQTT broker đang chạy tốt. Mở server `usp_web1.py` trên IP LAN của PC, có `--password`, `--no-browser`. Dùng web UI của Controller để thiết lập MQTT (broker, endpoint, topic), kết nối các Agent; không tự đoán topic/endpoint của DrayTek. Android chỉ kết nối với HTTP API của Controller, không kết nối trực tiếp MQTT. Mật khẩu HTTP là mật khẩu `--password`, khác MQTT password. Không mang config/mật khẩu/Agent ID thật từ ZIP nguồn vào gói mới.

Nếu dùng thư mục backend kèm theo và Python mới: `python -m pip install -r requirements.txt`. Proto do bạn của bạn cung cấp được sinh với Protobuf 7.35.1; cần runtime tương thích. Nếu không cài được runtime phù hợp, dùng chính môi trường PC đã chạy tốt, hoặc tái sinh module từ hai .proto bằng protoc tương thích (không chỉ đổi số version). Demo không cần các thư viện này.

Backend hiện có một schema hoạt động tại mỗi thời điểm (tự chọn theo config/mtime): `ap905.schema` và `uploaded.schema` là hai nguồn riêng. Web UI có chức năng upload schema để chọn đúng model. Chưa triển khai mapping schema theo từng Agent; không mặc định mọi path đều hỗ trợ mọi model.

## 6. Giới hạn và an toàn
- Đây là bản thử nghiệm, chưa dành cho Internet. Backend gốc sử dụng HTTP Basic, không TLS. Android cho phép HTTP chỉ với địa chỉ IPv4 private/local; HTTPS được xác thực theo CA hệ thống. Để chạy production cần reverse proxy HTTPS, VPN/ACL, tài khoản phân quyền, không công khai backend trên WAN, kiểm soát đọc dữ liệu nhạy cảm; không bật bỏ kiểm tra TLS. Mật khẩu app chỉ giữ trong RAM.
- Backend gốc vẫn có API SET/OPERATE; app v0.1 không gọi chúng. Vì chưa có cơ chế RBAC, bất kỳ người nào giữ mật khẩu backend gốc đều có thể sử dụng API ghi, do đó chỉ dùng trong lab hoặc demo.
- Multi-device monitoring: scan song song ở backend, chọn nhiều thiết bị trong app để xem trước profile; chưa có quản lý Site thực, lưu lịch sử traffic, topology, profile áp dụng/rollback, push notification, firmware, bản EN, APK release. Không tạo số liệu giả ngoài Demo.
- Backend giữ nguyên logic giao thức đã được bạn cung cấp, chưa xác nhận trên thiết bị thật trong môi trường này.
- `python backend/test_demo.py` chạy kiểm thử tích hợp localhost demo (không cần thiết bị thật).
