# Aleph Skill

**Agent Skill mô phỏng dòng thời gian dựa trên chứng cứ: đứng tại một điểm thay đổi, mở ra những nhánh quá khứ–hiện tại–tương lai khả dĩ, rồi buộc từng thế giới giả lập phải chịu trách nhiệm trước nguồn, cơ chế nhân quả và độ bất định.**

[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-skill?sort=semver)](https://github.com/d-init-d/aleph-skill/releases)

Aleph Skill dùng cho lịch sử phản thực, thay đổi ở hiện tại để dự phóng tương lai, timeline lai từ quá khứ qua hiện tại giả định tới tương lai, hiệu ứng cánh bướm, causal graph, human decision nodes, propagation, branch audit và báo cáo có phân tách rõ `fact`, `inference`, `simulation`, `counterfactual`.

## Nên dùng cùng D Research

Skill đạt chất lượng tốt nhất khi dùng cùng [`d-research-skill`](https://github.com/d-init-d/d-research-skill). D Research phụ trách tìm nguồn, dựng evidence ledger, kiểm tra mâu thuẫn và nghiên cứu con người trong vai trò công khai.

Nếu D Research chưa được cài khi gọi skill, agent chỉ hỏi một lần xem người dùng có muốn cài hoặc bật không. Nếu người dùng từ chối, mô phỏng vẫn tiếp tục ở chế độ giới hạn, phải ghi `research_quality: limited` và hạ confidence.

## Human node: bắt buộc tách research và roleplay

Với mỗi người ra quyết định có ảnh hưởng đáng kể:

1. **Human Research track** dùng D Research để dựng hồ sơ public-role có nguồn; tuyệt đối không nhập vai.
2. **Human Roleplay track** chỉ nhận dossier đã đóng băng và thông tin tồn tại tại thời điểm mô phỏng; không duyệt web, không tìm nguồn, không bịa động cơ riêng tư.
3. **Main simulator** đối chiếu giả thuyết nhập vai với chứng cứ, giữ các hành động thay thế và gán xác suất thận trọng.

Nếu runtime có task/subagent tool, hai track phải chạy bằng hai subagent khác nhau cho từng nhân vật material; roleplay chỉ bắt đầu sau khi research hoàn tất. Nếu runtime không có subagent, agent phải ghi rõ lý do và chạy hai pass cô lập. Mọi lần thực thi được lưu trong `human-track-ledger.jsonl`; chỉ viết rằng “đã tách track” là chưa đủ.

## Quality gates v1.2

- Tự đánh giá độ phức tạp theo thời gian, số miền, địa lý, tác nhân, độ sâu nhân quả, độ bất định và mức độ hệ trọng.
- Không giới hạn thời gian, số nguồn hoặc số vòng sửa; chỉ dừng khi phủ đủ critical questions và đạt evidence saturation.
- Tự suy ra ba dạng timeline: quá khứ→mốc lịch sử, hiện tại→tương lai, và quá khứ→hiện tại giả định→tương lai.
- Nhánh tương lai bắt buộc có leading indicators và disconfirming conditions.
- Kiểm tra source tier, trạng thái truy cập trực tiếp và contradiction pass.
- Cross-reference chặt giữa evidence, node, edge, actor, branch và propagation trace.
- Kiểm tra timestamp, agent reference, knowledge cutoff và phân phối giả thuyết của human tracks.
- Final validation bắt buộc có report và quality score theo thang 100.

Với một workspace đã hoàn tất:

```powershell
python scripts\validate_simulation_artifacts.py --workspace <run-dir> --mode draft --write-report
python scripts\render_simulation_report.py --workspace <run-dir>
python scripts\validate_simulation_artifacts.py --workspace <run-dir> --mode final --require-report --write-report
python scripts\render_simulation_report.py --workspace <run-dir>
python scripts\evaluate_simulation_quality.py --workspace <run-dir> --threshold 90 --enforce
```

## Cài đặt và kiểm tra

```powershell
git clone https://github.com/d-init-d/aleph-skill.git
cd aleph-skill
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python scripts\preflight.py --json
npm run self-test
```

Đường dẫn hỗ trợ:

- Codex: `~/.codex/skills/aleph-skill`
- Claude Code: `~/.claude/skills/aleph-skill` hoặc `.claude/skills/aleph-skill`
- OpenCode: `~/.config/opencode/skills/aleph-skill` hoặc `.opencode/skills/aleph-skill`
- Generic Agent Skills: `~/.agents/skills/aleph-skill` hoặc `.agents/skills/aleph-skill`

## Giới hạn an toàn

Không dùng skill để tuyên bố một tương lai chắc chắn, profiling người riêng tư, doxxing, stalking, xử lý dữ liệu trẻ vị thành niên, tài khoản riêng tư, vượt captcha/paywall/access control hoặc suy đoán đặc điểm nhạy cảm không có chứng cứ.
