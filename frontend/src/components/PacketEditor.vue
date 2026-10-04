<script setup>
import { computed } from 'vue'

const props = defineProps({
  packets: { type: Array, required: true },
  types: { type: Array, required: true },
  max: { type: Number, default: 48 }
})

const DIR_BY_TYPE = {
  CONNECT: ['C2S'],
  CONNACK: ['S2C'],
  PUBLISH: ['C2S'],
  PUBREC: ['S2C'],
  PUBREL: ['C2S'],
  PUBCOMP: ['S2C'],
  DISCONNECT: ['C2S', 'S2C']
}

function defaultDirection(type) {
  return DIR_BY_TYPE[type]?.[0] ?? 'C2S'
}

function onTypeChange(idx) {
  const p = props.packets[idx]
  const dirs = DIR_BY_TYPE[p.type]
  if (!dirs.includes(p.direction)) p.direction = defaultDirection(p.type)
}

function remove(idx) {
  props.packets.splice(idx, 1)
}

function move(idx, delta) {
  const j = idx + delta
  if (j < 0 || j >= props.packets.length) return
  const arr = props.packets
  const tmp = arr[idx]
  arr[idx] = arr[j]
  arr[j] = tmp
}

const rows = computed(() => props.packets)
</script>

<template>
  <table class="editor">
    <thead>
      <tr>
        <th style="width: 42px">#</th>
        <th style="width: 130px">控制包</th>
        <th style="width: 110px">方向</th>
        <th style="width: 110px">包标识</th>
        <th>字段</th>
        <th style="width: 90px">操作</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(p, idx) in rows" :key="idx">
        <td class="idx">{{ idx + 1 }}</td>
        <td>
          <select v-model="p.type" @change="onTypeChange(idx)">
            <option v-for="t in types" :key="t" :value="t">{{ t }}</option>
          </select>
        </td>
        <td>
          <select v-model="p.direction">
            <option
              v-for="d in DIR_BY_TYPE[p.type] || ['C2S', 'S2C']"
              :key="d" :value="d">{{ d }}</option>
          </select>
        </td>
        <td>
          <input
            v-if="['PUBLISH', 'PUBREC', 'PUBREL', 'PUBCOMP'].includes(p.type)"
            v-model.number="p.packet_id" type="number" min="1" max="65535"
            placeholder="1-65535" />
          <span v-else class="dim">—</span>
        </td>
        <td class="fields">
          <template v-if="p.type === 'CONNECT'">
            <label class="inline">
              <input type="checkbox" v-model="p.clean_start" />
              Clean Start = 1（清除持久会话）
            </label>
          </template>
          <template v-else-if="p.type === 'CONNACK'">
            <label class="inline">
              <input type="checkbox" v-model="p.session_present" />
              Session Present
            </label>
            <label class="inline small">
              原因码
              <input v-model.number="p.reason_code" type="number"
                min="0" max="255" style="width: 80px" placeholder="0" />
            </label>
          </template>
          <template v-else-if="p.type === 'PUBLISH'">
            <label class="inline">
              <input type="checkbox" v-model="p.dup" /> DUP
            </label>
            <span class="tag">QoS 2</span>
            <input v-model="p.topic" placeholder="主题 topic" style="width: 150px" />
            <input v-model="p.payload" placeholder="载荷 payload" style="width: 170px" />
          </template>
          <template v-else-if="['PUBREC', 'PUBREL', 'PUBCOMP'].includes(p.type)">
            <label class="inline small">
              原因码（可选）
              <input v-model.number="p.reason_code" type="number"
                min="0" max="255" style="width: 80px" placeholder="0" />
            </label>
          </template>
          <template v-else-if="p.type === 'DISCONNECT'">
            <label class="inline small">
              原因码（可选）
              <input v-model.number="p.reason_code" type="number"
                min="0" max="255" style="width: 80px" placeholder="0" />
            </label>
            <span v-if="p.direction === 'S2C'" class="tag warn">
              服务端断链 = 中继断链
            </span>
          </template>
        </td>
        <td class="ops">
          <button type="button" class="mini" :disabled="idx === 0"
            @click="move(idx, -1)" title="上移">↑</button>
          <button type="button" class="mini"
            :disabled="idx === packets.length - 1"
            @click="move(idx, 1)" title="下移">↓</button>
          <button type="button" class="mini danger" @click="remove(idx)"
            title="删除">×</button>
        </td>
      </tr>
      <tr v-if="!packets.length">
        <td colspan="6" class="empty">
          暂无控制包，点击“+ 控制包”或选择场景模板（最多 {{ max }} 条，按捕获顺序排列）
        </td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.editor { width: 100%; border-collapse: collapse; font-size: 13px; }
.editor th {
  text-align: left;
  color: var(--muted);
  font-weight: 500;
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
}
.editor td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
}
.idx { color: var(--muted); }
.dim { color: var(--muted); }
.fields { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.inline {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 5px;
  color: var(--muted);
  white-space: nowrap;
}
.inline.small { font-size: 12px; }
.tag {
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 1px 7px;
  font-size: 12px;
  color: var(--accent);
}
.tag.warn { color: var(--warn); border-color: var(--warn); }
.ops { white-space: nowrap; }
.mini {
  padding: 2px 8px;
  margin: 0 1px;
  font-size: 12px;
}
.mini.danger { color: var(--bad); }
.empty {
  text-align: center;
  color: var(--muted);
  padding: 22px;
}
select, input[type="number"] { width: 100%; }
input[type="checkbox"] { width: auto; }
</style>
