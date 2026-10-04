<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import PacketEditor from './components/PacketEditor.vue'

const TYPES = ['CONNECT', 'CONNACK', 'PUBLISH', 'PUBREC', 'PUBREL', 'PUBCOMP', 'DISCONNECT']
const MAX_PACKETS = 48

const form = reactive({
  audit_id: '',
  client_id: '',
  packets: []
})

const result = ref(null)
const conflict = ref(null)
const error = ref('')
const busy = ref(false)
const health = ref('检查中…')
const reopenId = ref('')

const canSubmit = computed(
  () =>
    form.audit_id.trim() &&
    form.client_id.trim() &&
    form.packets.length > 0 &&
    form.packets.length <= MAX_PACKETS &&
    !busy.value
)

function blankPacket(type = 'CONNECT') {
  return {
    type,
    direction: type === 'CONNACK' || type === 'PUBREC' || type === 'PUBCOMP'
      ? 'S2C'
      : 'C2S',
    packet_id: null,
    dup: false,
    qos: 2,
    topic: '',
    payload: '',
    clean_start: false,
    session_present: false,
    reason_code: null
  }
}

// 仅提交与控制包类型相关的字段，避免“载荷字段串扰”
function cleanPacket(p) {
  const out = { type: p.type, direction: p.direction }
  if (p.type === 'CONNECT') {
    out.clean_start = !!p.clean_start
  } else if (p.type === 'CONNACK') {
    out.session_present = !!p.session_present
    if (p.reason_code !== null && p.reason_code !== '')
      out.reason_code = Number(p.reason_code)
  } else if (p.type === 'PUBLISH') {
    out.qos = 2
    out.dup = !!p.dup
    if (p.packet_id !== null && p.packet_id !== '')
      out.packet_id = Number(p.packet_id)
    if (p.topic) out.topic = p.topic
    if (p.payload) out.payload = p.payload
  } else if (['PUBREC', 'PUBREL', 'PUBCOMP'].includes(p.type)) {
    if (p.packet_id !== null && p.packet_id !== '')
      out.packet_id = Number(p.packet_id)
    if (p.reason_code !== null && p.reason_code !== '')
      out.reason_code = Number(p.reason_code)
  } else if (p.type === 'DISCONNECT') {
    if (p.reason_code !== null && p.reason_code !== '')
      out.reason_code = Number(p.reason_code)
  }
  return out
}

function body() {
  return {
    audit_id: form.audit_id.trim(),
    client_id: form.client_id.trim(),
    packets: form.packets.map(cleanPacket)
  }
}

async function submit() {
  error.value = ''
  conflict.value = null
  busy.value = true
  try {
    const res = await fetch('/api/audits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body())
    })
    const data = await res.json()
    if (res.status === 409) {
      conflict.value = data
      result.value = null
      return
    }
    if (!res.ok) {
      error.value = JSON.stringify(data?.detail ?? data)
      return
    }
    result.value = data
    conflict.value = null
  } catch (e) {
    error.value = String(e)
  } finally {
    busy.value = false
  }
}

async function reopen() {
  error.value = ''
  conflict.value = null
  const id = reopenId.value.trim() || form.audit_id.trim()
  if (!id) {
    error.value = '请输入要重新打开的审计标识'
    return
  }
  busy.value = true
  try {
    const res = await fetch(`/api/audits/${encodeURIComponent(id)}`)
    if (!res.ok) {
      error.value = `审计标识 ${id} 不存在（${res.status}）`
      return
    }
    const data = await res.json()
    form.audit_id = data.audit_id
    form.client_id = data.client_id
    form.packets = (data.submission.packets || []).map((p) => ({
      ...blankPacket(p.type),
      ...p
    }))
    result.value = {
      replayed: true,
      audit_id: data.audit_id,
      client_id: data.client_id,
      fingerprint: data.fingerprint,
      verdict: data.verdict
    }
  } catch (e) {
    error.value = String(e)
  } finally {
    busy.value = false
  }
}

// ---- 场景模板 ----
function pushQos2(base, pid, { dup = false, payload = 'temp=21.5' } = {}) {
  base.push({ type: 'PUBLISH', direction: 'C2S', packet_id: pid, qos: 2, dup,
    topic: 'sat/tm/temp', payload, clean_start: false, session_present: false,
    reason_code: null })
  base.push({ type: 'PUBREC', direction: 'S2C', packet_id: pid })
  base.push({ type: 'PUBREL', direction: 'C2S', packet_id: pid })
  base.push({ type: 'PUBCOMP', direction: 'S2C', packet_id: pid })
}

function templateLegalResend() {
  form.packets = []
  form.packets.push({ ...blankPacket('CONNECT'), clean_start: false })
  form.packets.push({ ...blankPacket('CONNACK'), session_present: false })
  form.packets.push({ type: 'PUBLISH', direction: 'C2S', packet_id: 101, qos: 2,
    dup: false, topic: 'sat/tm/temp', payload: 'temp=21.5',
    clean_start: false, session_present: false, reason_code: null })
  // PUBREC 丢失 -> 中继断链
  form.packets.push({ ...blankPacket('DISCONNECT'), direction: 'S2C' })
  form.packets.push({ ...blankPacket('CONNECT'), clean_start: false })
  form.packets.push({ ...blankPacket('CONNACK'), session_present: true })
  form.packets.push({ type: 'PUBLISH', direction: 'C2S', packet_id: 101, qos: 2,
    dup: true, topic: 'sat/tm/temp', payload: 'temp=21.5',
    clean_start: false, session_present: false, reason_code: null })
  form.packets.push({ type: 'PUBREC', direction: 'S2C', packet_id: 101 })
  form.packets.push({ type: 'PUBREL', direction: 'C2S', packet_id: 101 })
  form.packets.push({ type: 'PUBCOMP', direction: 'S2C', packet_id: 101 })
}

function templateOpenSession() {
  form.packets = []
  form.packets.push({ ...blankPacket('CONNECT') })
  form.packets.push({ ...blankPacket('CONNACK') })
  form.packets.push({ type: 'PUBLISH', direction: 'C2S', packet_id: 202, qos: 2,
    dup: false, topic: 'sat/tm/volt', payload: 'v=28',
    clean_start: false, session_present: false, reason_code: null })
  form.packets.push({ type: 'PUBREC', direction: 'S2C', packet_id: 202 })
  form.packets.push({ type: 'PUBREL', direction: 'C2S', packet_id: 202 })
  form.packets.push({ ...blankPacket('DISCONNECT'), direction: 'S2C' })
}

function templateDuplicate() {
  form.packets = []
  form.packets.push({ ...blankPacket('CONNECT') })
  form.packets.push({ ...blankPacket('CONNACK') })
  pushQos2(form.packets, 303)
  form.packets.push({ type: 'PUBLISH', direction: 'C2S', packet_id: 303, qos: 2,
    dup: true, topic: 'sat/tm/temp', payload: 'temp=21.5',
    clean_start: false, session_present: false, reason_code: null })
}

function templateConflict() {
  templateLegalResend()
  // 改动重发载荷，制造冲突
  const dup = form.packets.find((p) => p.type === 'PUBLISH' && p.dup)
  dup.payload = 'temp=99.9-tampered'
}

function clearAll() {
  form.packets = []
  result.value = null
  conflict.value = null
  error.value = ''
}

onMounted(async () => {
  try {
    const res = await fetch('/api/health')
    health.value = res.ok ? '服务正常' : `异常 ${res.status}`
  } catch {
    health.value = '服务不可达'
  }
})
</script>

<template>
  <div class="page">
    <header>
      <h1>星载遥测中继 · MQTT 5 QoS2 重放审计</h1>
      <span class="health" :data-ok="health === '服务正常'">
        健康检查：{{ health }}
      </span>
    </header>

    <section class="card">
      <h2>1. 审计录入</h2>
      <div class="grid2">
        <label>
          稳定审计标识
          <input v-model="form.audit_id" maxlength="128"
            placeholder="如 AUDIT-2026-1004-001" />
        </label>
        <label>
          客户端标识（Client Identifier）
          <input v-model="form.client_id" maxlength="236" placeholder="如 SAT-AOCS-07" />
        </label>
      </div>

      <div class="toolbar">
        <button type="button" @click="form.packets.push(blankPacket('CONNECT'))"
          :disabled="form.packets.length >= MAX_PACKETS">
          + 控制包
        </button>
        <button type="button" class="ghost" @click="templateLegalResend">
          模板：断链合法重发（仅交付一次）
        </button>
        <button type="button" class="ghost" @click="templateOpenSession">
          模板：丢失确认的未闭合会话
        </button>
        <button type="button" class="ghost" @click="templateDuplicate">
          模板：闭合后冲突重发
        </button>
        <button type="button" class="ghost" @click="templateConflict">
          模板：重发载荷冲突
        </button>
        <button type="button" class="danger ghost" @click="clearAll">清空</button>
        <span class="counter" :data-over="form.packets.length > MAX_PACKETS">
          {{ form.packets.length }} / {{ MAX_PACKETS }}
        </span>
      </div>

      <PacketEditor :packets="form.packets" :types="TYPES" :max="MAX_PACKETS" />

      <div class="actions">
        <button class="primary" :disabled="!canSubmit" @click="submit">
          提交裁决
        </button>
        <span class="hint">
          相同审计标识 + 完全相同输入将回放原裁决；改动输入后复用该标识会被拒绝，原证据保留。
        </span>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <div v-if="conflict" class="conflict">
        <strong>已拒绝：{{ conflict.error }}</strong>
        <p>{{ conflict.message }}</p>
        <p>
          原裁决结论：<code>{{ conflict.existing_verdict }}</code>
          ，原指纹：<code>{{ conflict.existing_fingerprint.slice(0, 16) }}…</code>
        </p>
        <button type="button" class="ghost" @click="reopenId = form.audit_id; reopen()">
          按该审计标识重新打开原证据
        </button>
      </div>
    </section>

    <section class="card">
      <h2>2. 按审计标识重新打开</h2>
      <div class="reopen">
        <input v-model="reopenId" placeholder="输入历史审计标识" />
        <button type="button" :disabled="busy" @click="reopen">重新打开裁决</button>
      </div>
    </section>

    <section v-if="result" class="card">
      <h2>
        3. 裁决结果
        <span class="badge" :data-v="result.verdict.verdict">
          {{ result.verdict.verdict }}
        </span>
        <span v-if="result.replayed" class="replayed">（历史裁决回放）</span>
      </h2>

      <div class="summary">
        <div>遥测发布数：{{ result.verdict.summary.total_publications }}</div>
        <div>恰好交付一次：{{ result.verdict.summary.delivered_once }}</div>
        <div>未闭合交换：{{ result.verdict.summary.open_count }}</div>
        <div :data-bad="result.verdict.duplicate_delivery">
          重复交付：{{ result.verdict.duplicate_delivery ? '是' : '否' }}
        </div>
        <div class="fp">指纹：<code>{{ result.fingerprint.slice(0, 24) }}…</code></div>
      </div>

      <div v-if="result.verdict.first_violation" class="violation">
        首个违规依据 — 第 {{ result.verdict.first_violation.index }} 包：
        <code>{{ result.verdict.first_violation.code }}</code>
        {{ result.verdict.first_violation.message }}
      </div>

      <div v-if="result.verdict.open_exchanges.length" class="open-list">
        未闭合会话：
        <ul>
          <li v-for="o in result.verdict.open_exchanges" :key="o.packet_id">
            包标识 {{ o.packet_id }}，阶段 {{ o.state }}
            （首交于第 {{ o.first_index }} 包{{ o.replayed ? '，曾重放' : '' }}）
          </li>
        </ul>
      </div>

      <table class="evidence">
        <thead>
          <tr>
            <th>#</th><th>控制包</th><th>方向</th><th>会话阶段</th>
            <th>包标识</th><th>首次交付</th><th>裁决说明 / 首个违规依据</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in result.verdict.packets" :key="row.index"
            :class="{
              bad: row.violation,
              skip: !row.checked,
              first: result.verdict.first_violation?.index === row.index
            }">
            <td>{{ row.index }}</td>
            <td>{{ row.type }}</td>
            <td>{{ row.direction }}</td>
            <td>{{ row.session_phase }}</td>
            <td>{{ row.packet_id ?? '—' }}</td>
            <td>
              <span v-if="row.first_delivery === true" class="yes">是</span>
              <span v-else-if="row.first_delivery === false" class="no">否（重发）</span>
              <span v-else>—</span>
            </td>
            <td>
              <template v-if="row.violation">
                <code>{{ row.violation }}</code>：{{ row.violation_message }}
              </template>
              <template v-else>{{ row.note || '—' }}</template>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style>
:root {
  --bg: #0f1622;
  --card: #182234;
  --line: #2a3850;
  --text: #e7edf6;
  --muted: #93a3bb;
  --accent: #4f9dff;
  --ok: #38c172;
  --warn: #f5a623;
  --bad: #ff5d5d;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 14px/1.5 "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
}
.page { max-width: 1080px; margin: 0 auto; padding: 24px 20px 64px; }
header { display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap; }
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 16px; margin: 0 0 14px; }
.health { font-size: 12px; color: var(--muted); }
.health[data-ok="true"] { color: var(--ok); }
.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 18px;
  margin-top: 18px;
}
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
label { display: flex; flex-direction: column; gap: 6px; color: var(--muted); font-size: 13px; }
input, select {
  background: #101826;
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 9px;
  font-size: 13px;
}
input:focus, select:focus { outline: 1px solid var(--accent); }
.toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin: 14px 0; align-items: center; }
button {
  background: #233149;
  color: var(--text);
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 7px 12px;
  cursor: pointer;
  font-size: 13px;
}
button:hover:not(:disabled) { border-color: var(--accent); }
button:disabled { opacity: .45; cursor: not-allowed; }
button.primary { background: var(--accent); border-color: var(--accent); color: #071426; font-weight: 600; }
button.ghost { background: transparent; color: var(--muted); }
button.danger { color: var(--bad); }
.counter { margin-left: auto; color: var(--muted); font-size: 12px; }
.counter[data-over="true"] { color: var(--bad); }
.actions { display: flex; align-items: center; gap: 12px; margin-top: 14px; }
.hint { color: var(--muted); font-size: 12px; }
.error, .conflict { color: var(--bad); font-size: 13px; margin-top: 10px; }
.conflict {
  border: 1px solid var(--bad);
  background: rgba(255, 93, 93, .08);
  border-radius: 8px;
  padding: 10px 14px;
}
.conflict p { margin: 6px 0; color: var(--text); }
.reopen { display: flex; gap: 8px; }
.reopen input { flex: 1; }
.badge {
  font-size: 12px;
  border-radius: 20px;
  padding: 2px 12px;
  margin-left: 8px;
  border: 1px solid currentColor;
}
.badge[data-v="ACCEPTED"] { color: var(--ok); }
.badge[data-v="OPEN"] { color: var(--warn); }
.badge[data-v="REJECTED"] { color: var(--bad); }
.replayed { color: var(--muted); font-size: 12px; }
.summary { display: flex; gap: 24px; flex-wrap: wrap; color: var(--muted); margin-bottom: 12px; }
.summary [data-bad="true"] { color: var(--bad); font-weight: 600; }
.fp { margin-left: auto; }
.violation {
  border-left: 3px solid var(--bad);
  background: rgba(255, 93, 93, .08);
  padding: 8px 12px;
  border-radius: 0 6px 6px 0;
  margin-bottom: 12px;
}
.open-list { color: var(--warn); margin-bottom: 12px; }
.open-list ul { margin: 6px 0 0; padding-left: 20px; }
table.evidence { width: 100%; border-collapse: collapse; font-size: 13px; }
.evidence th, .evidence td {
  border: 1px solid var(--line);
  padding: 6px 9px;
  text-align: left;
  vertical-align: top;
}
.evidence th { color: var(--muted); font-weight: 500; background: #101826; }
tr.bad { background: rgba(255, 93, 93, .07); }
tr.first td:first-child { box-shadow: inset 3px 0 0 var(--bad); font-weight: 600; }
tr.skip { opacity: .45; }
.yes { color: var(--ok); }
.no { color: var(--warn); }
code {
  background: #0c1320;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
  color: #9fc4ff;
}
@media (max-width: 720px) { .grid2 { grid-template-columns: 1fr; } }
</style>
