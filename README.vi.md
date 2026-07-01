# Aleph Skill

**Agent Skill mô phỏng dòng thời gian dựa trên chứng cứ: đứng tại một điểm thay đổi, mở ra các nhánh quá khứ-hiện tại-tương lai khả dĩ, rồi buộc từng thế giới giả lập phải chịu trách nhiệm trước nguồn, cơ chế nhân-quả và độ bất định.**

[![Release](https://img.shields.io/github/v/release/d-init-d/aleph-skill?sort=semver)](https://github.com/d-init-d/aleph-skill/releases)

Aleph Skill dùng cho counterfactual history, hiệu ứng cánh bướm, causal graph, human decision nodes, propagation, branch audit, và báo cáo mô phỏng có phân tách rõ `fact`, `inference`, `simulation`, `counterfactual`.

## Nên dùng cùng D Research

Skill này được thiết kế để đạt chất lượng tốt nhất khi đi cùng [`d-research-skill`](https://github.com/d-init-d/d-research-skill).

D Research nên phụ trách tìm nguồn, dựng evidence ledger, kiểm tra mâu thuẫn, và nghiên cứu public-role actor. Nếu khi gọi skill mà chưa có D Research, agent nên hỏi người dùng một lần xem có muốn cài/bật D Research không. Nếu người dùng từ chối, vẫn chạy được nhưng phải đánh dấu `research_quality: basic` và hạ confidence.

## Human node: bắt buộc tách research và roleplay

Với nhân vật/người ra quyết định có ảnh hưởng đáng kể:

1. **Human Research track** - nghiên cứu hồ sơ public-role bằng D Research hoặc nguồn công khai; không nhập vai.
2. **Human Roleplay track** - chỉ dùng dossier đã hoàn thành và thông tin có tại thời điểm mô phỏng; không tìm nguồn, không bịa đời tư.
3. **Main simulator** - đối chiếu roleplay với chứng cứ, tạo nhánh thay thế, gán xác suất thận trọng, và luôn đánh dấu roleplay là `simulation`, không phải `fact`.

Nếu runtime cho phép subagent và người dùng đồng ý, chạy research/roleplay bằng subagent riêng. Nếu không, vẫn phải giữ thành hai pass tách biệt trong cùng ngữ cảnh.

## Cài đặt

```powershell
git clone https://github.com/d-init-d/aleph-skill.git
cd aleph-skill
```

Kiểm tra:

```powershell
python scripts\validate_skill_package.py .
python scripts\validate_simulation_artifacts.py --examples
python scripts\preflight.py --json
npm run self-test
```

Đường dẫn skill:

- Codex: `~/.codex/skills/aleph-skill`
- Claude Code: `~/.claude/skills/aleph-skill` hoặc `.claude/skills/aleph-skill`
- OpenCode: `~/.config/opencode/skills/aleph-skill` hoặc `.opencode/skills/aleph-skill`
- Generic Agent Skills: `~/.agents/skills/aleph-skill` hoặc `.agents/skills/aleph-skill`

## Prompt mẫu

```text
Use $aleph-skill to simulate an oil price +40% shock starting June 2026.
Focus on inflation, central-bank reaction, growth, shipping, and emerging markets over 24 months.
Use D Research for evidence, split material human decisions into research and roleplay tracks,
and produce at least three branches with probabilities and uncertainty warnings.
```

## Giới hạn an toàn

Không dùng skill này để tuyên bố một tương lai chắc chắn, profiling người riêng tư, doxxing, stalking, xử lý dữ liệu trẻ vị thành niên, tài khoản riêng tư, bypass captcha/paywall/access-control, hoặc suy đoán nhạy cảm không có chứng cứ.
