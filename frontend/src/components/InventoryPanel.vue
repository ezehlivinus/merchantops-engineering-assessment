<script setup>
import { computed, nextTick, ref } from 'vue'
import { products } from '../api'
import { useResource } from '../useResource'
import PaginationControls from './PaginationControls.vue'
import RequestState from './RequestState.vue'

const props = defineProps({
  productId: { type: String, required: true },
  variants: { type: Array, required: true },
})

// Local copy: adjustments update these rows without touching the editor's
// product record (replacing it would reset unsaved General-tab edits).
const rows = ref(props.variants.map((variant) => ({
  ...variant,
  inventory: variant.inventory && { ...variant.inventory },
})))

const editing = ref(null)
const delta = ref('')
const reason = ref('')
const submitting = ref(false)
const feedback = ref(null)

const editingRow = computed(() => rows.value.find((row) => row.id === editing.value))
const newQuantity = computed(() => {
  if (!editingRow.value || !Number.isInteger(delta.value)) return null
  return editingRow.value.inventory.quantity + delta.value
})
// Client-side hint only; the server enforces both rules.
const problem = computed(() => {
  if (delta.value === 0) return 'Enter a nonzero whole number.'
  if (newQuantity.value !== null && newQuantity.value < editingRow.value.inventory.reserved) {
    return `Below the ${editingRow.value.inventory.reserved} reserved units.`
  }
  return ''
})

// History: one variant at a time, loaded on demand.
const historyFor = ref(null)
const historyPage = ref(1)
const { data: history, loading: historyLoading, error: historyError, load } = useResource()
const timestamp = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

function loadHistory(page = historyPage.value) {
  historyPage.value = page
  const variantId = historyFor.value
  load((signal) => products.adjustments(props.productId, variantId, page, signal))
}

function toggleHistory(row) {
  if (historyFor.value === row.id) {
    historyFor.value = null
    return
  }
  historyFor.value = row.id
  loadHistory(1)
}

function signed(value) {
  return value > 0 ? '+' + value : String(value).replace('-', '−')
}

async function open(row) {
  editing.value = row.id
  delta.value = ''
  reason.value = ''
  feedback.value = null
  await nextTick()
  document.getElementById('adjust-delta')?.focus()
}

function close() {
  editing.value = null
}

async function submit(row) {
  if (submitting.value) return
  submitting.value = true
  feedback.value = null
  try {
    const result = await products.adjustInventory(props.productId, row.id, {
      delta: delta.value,
      reason: reason.value,
    })
    row.inventory.quantity = result.quantity_after
    feedback.value = {
      ok: true,
      text: `${row.name} (${row.sku}): stock ${result.quantity_before} → ${result.quantity_after}.`,
    }
    editing.value = null
    if (historyFor.value === row.id) loadHistory(1)
  } catch (failure) {
    // A 409 carries the current stock: show it so the operator can retry.
    if (failure.status === 409 && Number.isInteger(failure.data?.quantity)) {
      row.inventory.quantity = failure.data.quantity
      row.inventory.reserved = failure.data.reserved
    }
    feedback.value = { ok: false, text: failure.message }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <h2 class="panel-heading">Variant inventory</h2>
  <div class="adjust-feedback">
    <p v-if="feedback?.ok" class="success-text" role="status">{{ feedback.text }}</p>
    <p v-else-if="feedback" class="error-text" role="alert">{{ feedback.text }}</p>
  </div>
  <div v-if="rows.length" class="table-scroll">
    <table class="data-table">
      <thead>
        <tr>
          <th scope="col">Variant</th><th scope="col">SKU</th><th scope="col" class="numeric">Quantity</th>
          <th scope="col" class="numeric">Reserved</th><th scope="col" class="numeric">Available</th>
          <th scope="col"><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="variant in rows" :key="variant.id">
          <tr>
            <th scope="row">{{ variant.name }}</th>
            <td class="code">{{ variant.sku }}</td>
            <td class="numeric">{{ variant.inventory?.quantity ?? '—' }}</td>
            <td class="numeric">{{ variant.inventory?.reserved ?? '—' }}</td>
            <td class="numeric">{{ variant.inventory ? variant.inventory.quantity - variant.inventory.reserved : '—' }}</td>
            <td class="numeric row-actions">
              <button class="button" type="button" :aria-expanded="historyFor === variant.id" @click="toggleHistory(variant)">
                History<span class="sr-only"> {{ variant.name }}</span>
              </button>
              <button class="button" type="button" :disabled="!variant.inventory || submitting"
                :aria-expanded="editing === variant.id" @click="editing === variant.id ? close() : open(variant)">
                Adjust<span class="sr-only"> {{ variant.name }}</span>
              </button>
            </td>
          </tr>
          <tr v-if="editing === variant.id" class="adjust-row">
            <td colspan="6">
              <form class="adjust-form" :aria-label="'Adjust stock for ' + variant.name" @submit.prevent="submit(variant)">
                <fieldset class="adjust-fields" :disabled="submitting">
                  <label class="field">Change
                    <input id="adjust-delta" v-model="delta" type="number" step="1" required placeholder="e.g. -3 or 5" />
                  </label>
                  <label class="field adjust-reason">Reason
                    <input v-model="reason" required maxlength="255" placeholder="e.g. Cycle count correction" />
                  </label>
                  <p class="adjust-preview" aria-live="polite">
                    New quantity: <strong>{{ newQuantity ?? '—' }}</strong>
                    <span v-if="problem" class="error-text"> · {{ problem }}</span>
                  </p>
                  <button class="button" type="button" @click="close">Cancel</button>
                  <button class="button button-primary" type="submit" :disabled="!!problem">{{ submitting ? 'Saving…' : 'Save adjustment' }}</button>
                </fieldset>
              </form>
            </td>
          </tr>
          <tr v-if="historyFor === variant.id" class="adjust-row">
            <td colspan="6">
              <section class="history" :aria-label="'Stock adjustments for ' + variant.name" :aria-busy="historyLoading">
                <RequestState :loading="historyLoading" :error="historyError" label="adjustments" @retry="loadHistory()" />
                <template v-if="history">
                  <table v-if="history.results.length" class="data-table history-table">
                    <thead>
                      <tr>
                        <th scope="col">When</th><th scope="col">Operator</th><th scope="col" class="numeric">Change</th>
                        <th scope="col" class="numeric">Stock</th><th scope="col">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="entry in history.results" :key="entry.id">
                        <td><time :datetime="entry.created_at">{{ timestamp.format(new Date(entry.created_at)) }}</time></td>
                        <td>{{ entry.actor }}</td>
                        <td class="numeric" :class="entry.delta > 0 ? 'change-up' : 'change-down'">{{ signed(entry.delta) }}</td>
                        <td class="numeric">{{ entry.quantity_before }} → {{ entry.quantity_after }}</td>
                        <td class="history-reason">{{ entry.reason }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-else class="request-state">No adjustments recorded for this variant yet.</p>
                  <PaginationControls v-if="history.count > history.results.length || historyPage > 1" :page="historyPage"
                    :result="history" label="adjustments" singular="adjustment" @change="loadHistory" />
                </template>
              </section>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>
  <p v-else class="request-state">No variants for this product.</p>
</template>
