import { useId, useMemo, useState } from 'react'
import { ApiError } from '../../api/client'
import { exactKeys, isRecord, plainJSON } from '../generated/validation'
import '../generated/generated.css'

export type SchemaFormValue = {
  action: 'accept' | 'decline' | 'cancel'
  fields: Record<string, unknown>
}
export type SchemaFormProps = {
  schema: Record<string, unknown>
  onSubmit: (value: SchemaFormValue) => Promise<void>
  disabled?: boolean
  acceptedValue?: SchemaFormValue
}
type Scalar = string | number | boolean
function ownValue<T>(record: Record<string, T>, name: string): T | undefined {
  return Object.hasOwn(record, name) ? record[name] : undefined
}
type Field = {
  name: string
  label: string
  description?: string
  type: 'string' | 'integer' | 'number' | 'boolean' | 'array'
  required: boolean
  enum?: Scalar[]
  itemType?: 'string' | 'integer' | 'number' | 'boolean'
  minLength?: number
  maxLength?: number
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  minItems?: number
  maxItems?: number
  default?: Scalar | Scalar[]
}
export type FormInspection = {
  fields: Field[]
  title?: string
  description?: string
  reason?: string
}
const metadata = ['title', 'description', 'default', 'examples']
const credentialName = (name: string) => {
  const key = name
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/([A-Z])([A-Z][a-z])/g, '$1_$2')
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fff]+/g, '_')
  return (
    /(^|_)(password|passwd|pwd|secrets?|credentials?|api_?key|access_?key|private_?key|client_?secret|access_?token|refresh_?token|auth_?token|bearer_?token|session_?token|authorization)(_|$)/i.test(
      key,
    ) ||
    key === 'token' ||
    /密码|口令|密钥|令牌|凭据/.test(key)
  )
}
const typeMatches = (value: unknown, type: Field['type']): value is Scalar =>
  type === 'string'
    ? typeof value === 'string'
    : type === 'boolean'
      ? typeof value === 'boolean'
      : type === 'number'
        ? typeof value === 'number' && Number.isFinite(value)
        : type === 'integer'
          ? typeof value === 'number' && Number.isSafeInteger(value)
          : false
export function validateFieldValue(field: Field, value: unknown): string | null {
  if (value === undefined) return field.required ? '此项尚未回答。' : null
  if (field.type === 'array') {
    if (!Array.isArray(value) || !field.enum || !value.every((item) => field.enum?.includes(item)))
      return '请选择目录内的选项。'
    if (new Set(value).size !== value.length) return '选项不能重复。'
    if (field.minItems !== undefined && value.length < field.minItems)
      return `至少选择 ${field.minItems} 项。`
    if (field.maxItems !== undefined && value.length > field.maxItems)
      return `至多选择 ${field.maxItems} 项。`
    return null
  }
  if (!typeMatches(value, field.type))
    return field.type === 'integer'
      ? '请输入可安全表示的整数。'
      : field.type === 'number'
        ? '请输入有限数值。'
        : '回答类型不匹配。'
  if (field.enum && !field.enum.includes(value)) return '请选择目录内的选项。'
  if (typeof value === 'string') {
    const length = Array.from(value).length
    if (field.minLength !== undefined && length < field.minLength)
      return `至少 ${field.minLength} 个字符。`
    if (field.maxLength !== undefined && length > field.maxLength)
      return `至多 ${field.maxLength} 个字符。`
  }
  if (typeof value === 'number') {
    if (field.minimum !== undefined && value < field.minimum) return `不能小于 ${field.minimum}。`
    if (field.maximum !== undefined && value > field.maximum) return `不能大于 ${field.maximum}。`
    if (field.exclusiveMinimum !== undefined && value <= field.exclusiveMinimum)
      return `必须大于 ${field.exclusiveMinimum}。`
    if (field.exclusiveMaximum !== undefined && value >= field.exclusiveMaximum)
      return `必须小于 ${field.exclusiveMaximum}。`
  }
  return null
}
export function inspectFormSchema(schema: unknown): FormInspection {
  const fail = (reason: string): FormInspection => ({ fields: [], reason })
  if (!isRecord(schema) || schema.type !== 'object' || !isRecord(schema.properties))
    return fail('仅支持具有明确 properties 的顶层 object schema。')
  if (
    !exactKeys(schema, [
      'type',
      'properties',
      'required',
      'additionalProperties',
      'title',
      'description',
      '$schema',
      '$id',
    ])
  )
    return fail('此 schema 包含未支持的约束、引用或交互扩展，已降级只读。')
  if (schema.additionalProperties !== undefined && typeof schema.additionalProperties !== 'boolean')
    return fail('不支持动态附加字段 schema。')
  if (
    (schema.title !== undefined && typeof schema.title !== 'string') ||
    (schema.description !== undefined && typeof schema.description !== 'string')
  )
    return fail('表单标题或说明必须为文本。')
  if (
    (schema.$schema !== undefined && typeof schema.$schema !== 'string') ||
    (schema.$id !== undefined && typeof schema.$id !== 'string')
  )
    return fail('schema 标识无效。')
  if (
    schema.required !== undefined &&
    (!Array.isArray(schema.required) ||
      !schema.required.every((name) => typeof name === 'string') ||
      new Set(schema.required).size !== schema.required.length)
  )
    return fail('required 必须是唯一字段名数组。')
  const required = new Set<string>(schema.required as string[] | undefined)
  const entries = Object.entries(schema.properties)
  if (entries.length > 20) return fail('表单字段超过 20 个，已停用交互。')
  if ([...required].some((name) => !Object.hasOwn(schema.properties as object, name)))
    return fail('required 引用了未声明字段。')
  const fields: Field[] = []
  for (const [name, value] of entries) {
    if (!name || ['__proto__', 'prototype', 'constructor'].includes(name) || credentialName(name))
      return fail('表单包含凭据、口令或不安全字段名，不能在此收集。')
    if (!isRecord(value)) return fail(`字段 ${name} 的 schema 无效。`)
    if (
      value.writeOnly === true ||
      value.format === 'password' ||
      (typeof value.title === 'string' && credentialName(value.title))
    )
      return fail(`字段 ${name} 涉及秘密信息，不能在此收集。`)
    const type = value.type
    if (
      type !== 'string' &&
      type !== 'integer' &&
      type !== 'number' &&
      type !== 'boolean' &&
      type !== 'array'
    )
      return fail(`字段 ${name} 未明确声明支持的浅层类型。`)
    const specific =
      type === 'array'
        ? ['items', 'minItems', 'maxItems', 'uniqueItems']
        : type === 'string'
          ? ['enum', 'minLength', 'maxLength']
          : type === 'integer' || type === 'number'
            ? ['enum', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum']
            : ['enum']
    if (!exactKeys(value, ['type', ...metadata, 'readOnly', 'writeOnly', ...specific]))
      return fail(`字段 ${name} 含未支持约束（如深层、ref、format 或自定义交互）。`)
    if (
      (value.readOnly !== undefined && value.readOnly !== false) ||
      (value.writeOnly !== undefined && value.writeOnly !== false)
    )
      return fail(`字段 ${name} 含未支持的只读或写入语义。`)
    if (
      (value.title !== undefined && typeof value.title !== 'string') ||
      (value.description !== undefined && typeof value.description !== 'string')
    )
      return fail(`字段 ${name} 的标签或说明无效。`)
    const field: Field = {
      name,
      label: typeof value.title === 'string' ? value.title : name,
      type,
      required: required.has(name),
      ...(typeof value.description === 'string' ? { description: value.description } : {}),
    }
    for (const key of ['minLength', 'maxLength', 'minItems', 'maxItems'] as const) {
      if (value[key] !== undefined) {
        if (typeof value[key] !== 'number' || !Number.isSafeInteger(value[key]) || value[key] < 0)
          return fail(`字段 ${name} 的长度约束无效。`)
        field[key] = value[key]
      }
    }
    for (const key of ['minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum'] as const) {
      if (value[key] !== undefined) {
        if (typeof value[key] !== 'number' || !Number.isFinite(value[key]))
          return fail(`字段 ${name} 的范围约束无效。`)
        field[key] = value[key]
      }
    }
    if (
      (field.minLength !== undefined &&
        field.maxLength !== undefined &&
        field.minLength > field.maxLength) ||
      (field.minItems !== undefined &&
        field.maxItems !== undefined &&
        field.minItems > field.maxItems) ||
      (field.minimum !== undefined && field.maximum !== undefined && field.minimum > field.maximum)
    )
      return fail(`字段 ${name} 的约束相互冲突。`)
    if (type === 'array') {
      const items = value.items
      if (
        !isRecord(items) ||
        !exactKeys(items, ['type', 'enum']) ||
        (items.type !== 'string' &&
          items.type !== 'integer' &&
          items.type !== 'number' &&
          items.type !== 'boolean') ||
        !Array.isArray(items.enum)
      )
        return fail(`字段 ${name} 仅支持 items 明确类型及 enum 的浅层多选。`)
      field.itemType = items.type
      if (
        items.enum.length === 0 ||
        items.enum.length > 100 ||
        !items.enum.every((item) => typeMatches(item, items.type as Field['type'])) ||
        new Set(items.enum).size !== items.enum.length
      )
        return fail(`字段 ${name} 的多选枚举无效或超过 100 项。`)
      if (value.uniqueItems !== undefined && typeof value.uniqueItems !== 'boolean')
        return fail(`字段 ${name} 的 uniqueItems 无效。`)
      field.enum = items.enum
    } else if (value.enum !== undefined) {
      if (
        !Array.isArray(value.enum) ||
        value.enum.length === 0 ||
        value.enum.length > 100 ||
        !value.enum.every((item) => typeMatches(item, type)) ||
        new Set(value.enum).size !== value.enum.length
      )
        return fail(`字段 ${name} 的枚举无效或超过 100 项。`)
      field.enum = value.enum
    }
    if (Object.hasOwn(value, 'default')) {
      if (validateFieldValue(field, value.default))
        return fail(`字段 ${name} 的默认值不满足约束；未采用此默认。`)
      field.default = value.default as Scalar | Scalar[]
    }
    fields.push(field)
  }
  return {
    fields,
    ...(typeof schema.title === 'string' ? { title: schema.title } : {}),
    ...(typeof schema.description === 'string' ? { description: schema.description } : {}),
  }
}
function draftFields(fields: Field[]) {
  return Object.fromEntries(
    fields
      .filter((field) => field.default !== undefined)
      .map((field) => [
        field.name,
        Array.isArray(field.default) ? [...field.default] : field.default,
      ]),
  )
}
function FormBody({
  inspection,
  onSubmit,
  disabled = false,
  acceptedValue,
}: Omit<SchemaFormProps, 'schema'> & { inspection: FormInspection }) {
  const id = useId()
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    draftFields(inspection.fields),
  )
  const [numbers, setNumbers] = useState<Record<string, string>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<SchemaFormValue | null>(null)
  const [confirmed, setConfirmed] = useState<SchemaFormValue | null>(null)
  const [error, setError] = useState('')
  const received = acceptedValue ?? confirmed
  const locked = disabled || busy || !!pending || !!received
  async function submit(action: SchemaFormValue['action']) {
    if (disabled || busy || received) return
    const value = pending ?? {
      action,
      fields:
        action === 'accept'
          ? Object.fromEntries(
              inspection.fields
                .filter((field) => Object.hasOwn(values, field.name))
                .map((field) => [field.name, ownValue(values, field.name)]),
            )
          : {},
    }
    if (value.action === 'accept' && !pending) {
      const validation: Record<string, string> = {}
      for (const field of inspection.fields) {
        const reason = validateFieldValue(field, ownValue(values, field.name))
        if (reason) validation[field.name] = reason
      }
      setErrors(validation)
      if (Object.keys(validation).length) return
    }
    setPending(value)
    setBusy(true)
    setError('')
    try {
      await onSubmit(value)
      setConfirmed(value)
      setPending(null)
    } catch (reason) {
      const definitive = reason instanceof ApiError && reason.status < 500
      if (definitive) setPending(null)
      setError(
        `${reason instanceof Error ? reason.message : '提交未确认'}${definitive ? '' : ' 结果未确认，核对会使用同一次动作与原字段。'}`,
      )
    } finally {
      setBusy(false)
    }
  }
  function update(name: string, value: unknown) {
    setValues((previous) => {
      const next = { ...previous }
      if (value === undefined) delete next[name]
      else next[name] = value
      return next
    })
    setErrors((previous) => {
      const next = { ...previous }
      delete next[name]
      return next
    })
  }
  if (
    received &&
    (!isRecord(received.fields) || !['accept', 'decline', 'cancel'].includes(received.action))
  )
    return (
      <p role="status" className="wb-generated-error">
        已提交记录无法按当前表单读取，内容未展示。
      </p>
    )
  if (received) {
    const safe = Object.fromEntries(
      inspection.fields
        .filter((field) => Object.hasOwn(received.fields, field.name))
        .map((field) => [
          field.name,
          validateFieldValue(field, received.fields[field.name]) === null
            ? received.fields[field.name]
            : '[字段值不符合当前schema，已隐藏]',
        ]),
    )
    return (
      <div className="wb-schema-received" role="status">
        <p>
          {received.action === 'accept'
            ? '回答已收到'
            : received.action === 'decline'
              ? '拒绝已收到'
              : '取消已收到'}
        </p>
        {received.action === 'accept' && <pre>{plainJSON(safe)}</pre>}
      </div>
    )
  }
  return (
    <form
      noValidate
      className="wb-schema-form"
      onSubmit={(event) => {
        event.preventDefault()
        void submit('accept')
      }}
    >
      {inspection.title && <h3>{inspection.title}</h3>}
      {inspection.description && <p>{inspection.description}</p>}
      <p className="wb-generated-note">
        默认值仅是草稿，需要你明确提交。此表单不能用于输入密码或密钥。
      </p>
      {inspection.fields.map((field, index) => {
        const fieldId = `${id}-field-${index}`,
          descriptionId = `${fieldId}-help`,
          errorId = `${fieldId}-error`
        const described =
          [field.description ? descriptionId : '', ownValue(errors, field.name) ? errorId : '']
            .filter(Boolean)
            .join(' ') || undefined
        const value = ownValue(values, field.name)
        return (
          <div className="wb-schema-field" key={field.name}>
            {field.type === 'array' ? (
              <fieldset
                disabled={locked}
                aria-describedby={described}
                aria-invalid={!!ownValue(errors, field.name)}
              >
                <legend>
                  {field.label}
                  {field.required ? '（必填）' : ''}
                </legend>
                {field.enum?.map((option, optionIndex) => (
                  <label className="wb-schema-choice" key={optionIndex}>
                    <input
                      type="checkbox"
                      checked={Array.isArray(value) && value.includes(option)}
                      onChange={(event) => {
                        const current = Array.isArray(value) ? value : []
                        update(
                          field.name,
                          event.target.checked
                            ? [...current, option]
                            : current.filter((item) => item !== option),
                        )
                      }}
                    />
                    <span>{String(option)}</span>
                  </label>
                ))}
              </fieldset>
            ) : (
              <>
                <label htmlFor={fieldId}>
                  {field.label}
                  {field.required ? '（必填）' : ''}
                </label>
                {field.enum ? (
                  <select
                    id={fieldId}
                    disabled={locked}
                    required={field.required}
                    aria-invalid={!!ownValue(errors, field.name)}
                    aria-describedby={described}
                    value={
                      field.enum.findIndex((option) => option === value) < 0
                        ? ''
                        : String(field.enum.findIndex((option) => option === value))
                    }
                    onChange={(event) =>
                      update(
                        field.name,
                        event.target.value === ''
                          ? undefined
                          : field.enum?.[Number(event.target.value)],
                      )
                    }
                  >
                    <option value="">尚未选择</option>
                    {field.enum.map((option, optionIndex) => (
                      <option key={optionIndex} value={optionIndex}>
                        {String(option)}
                      </option>
                    ))}
                  </select>
                ) : field.type === 'boolean' ? (
                  <select
                    id={fieldId}
                    disabled={locked}
                    required={field.required}
                    aria-describedby={described}
                    aria-invalid={!!ownValue(errors, field.name)}
                    value={value === true ? 'true' : value === false ? 'false' : ''}
                    onChange={(event) =>
                      update(
                        field.name,
                        event.target.value === '' ? undefined : event.target.value === 'true',
                      )
                    }
                  >
                    <option value="">尚未回答</option>
                    <option value="true">是</option>
                    <option value="false">否</option>
                  </select>
                ) : (
                  <input
                    id={fieldId}
                    type={field.type === 'string' ? 'text' : 'number'}
                    step={field.type === 'integer' ? 1 : 'any'}
                    disabled={locked}
                    required={field.required}
                    autoComplete="off"
                    aria-describedby={described}
                    aria-invalid={!!ownValue(errors, field.name)}
                    value={
                      field.type === 'string'
                        ? typeof value === 'string'
                          ? value
                          : ''
                        : (ownValue(numbers, field.name) ??
                          (typeof value === 'number' ? String(value) : ''))
                    }
                    onChange={(event) => {
                      if (field.type === 'string') update(field.name, event.target.value)
                      else {
                        const raw = event.target.value
                        setNumbers((previous) => ({ ...previous, [field.name]: raw }))
                        update(field.name, raw === '' ? undefined : Number(raw))
                      }
                    }}
                  />
                )}
              </>
            )}
            {field.description && (
              <p id={descriptionId} className="wb-generated-note">
                {field.description}
              </p>
            )}
            {field.default !== undefined && (
              <small className="wb-generated-note">包含建议默认值，提交前可修改。</small>
            )}
            {ownValue(errors, field.name) && (
              <p role="alert" id={errorId} className="wb-generated-error">
                {ownValue(errors, field.name)}
              </p>
            )}
          </div>
        )
      })}
      {error && (
        <p role="alert" className="wb-generated-error">
          {error}
        </p>
      )}
      <div className="wb-generated-actions">
        {pending ? (
          <button
            type="button"
            disabled={disabled || busy}
            onClick={() => void submit(pending.action)}
          >
            {busy ? '正在提交…' : '核对本次提交'}
          </button>
        ) : (
          <>
            <button type="submit" disabled={locked}>
              提交回答
            </button>
            <button type="button" disabled={locked} onClick={() => void submit('decline')}>
              拒绝回答
            </button>
            <button type="button" disabled={locked} onClick={() => void submit('cancel')}>
              取消
            </button>
          </>
        )}
      </div>
    </form>
  )
}
export function SchemaForm(props: SchemaFormProps) {
  const inspection = useMemo(() => {
    try {
      return inspectFormSchema(props.schema)
    } catch {
      return { fields: [], reason: 'schema 无法安全读取，交互已停用。' }
    }
  }, [props.schema])
  if (inspection.reason)
    return (
      <section className="wb-schema-form wb-generated-fallback" role="status">
        <h3>此表单暂不能交互</h3>
        <p>{inspection.reason}</p>
        <p className="wb-generated-note">不会采集或提交未支持字段；请使用宿主持久问题流程处理。</p>
      </section>
    )
  // A changed schema is a new answer context; the host also keys this component by Question ID/version.
  return <FormBody key={JSON.stringify(inspection)} {...props} inspection={inspection} />
}
