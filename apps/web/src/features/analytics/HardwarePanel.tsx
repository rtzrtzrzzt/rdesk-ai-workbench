import { Cpu, Database, HardDrive, MemoryStick } from 'lucide-react'
import type { HardwareSnapshot } from '../../api/types'
import { Status } from '../../components/ui'
import { AnalyticsSection, bytesLabel, countLabel, numberLabel, utcLabel } from './ui'

function Meter({
  used,
  total,
  label,
}: {
  used: number | null
  total: number | null
  label: string
}) {
  return used !== null && total !== null && total > 0 ? (
    <progress value={used} max={total} aria-label={label} />
  ) : null
}
export function HardwarePanel({ snapshot }: { snapshot: HardwareSnapshot }) {
  const { memory, cpu, gpu, storage } = snapshot
  return (
    <AnalyticsSection
      title="硬件当前读数"
      description={`采集于 ${utcLabel(snapshot.sampled_at)} · ${snapshot.platform} / ${snapshot.architecture}。范围为服务端当前可见资源，不随调用筛选变化。`}
    >
      <div className="analytics-hardware-grid">
        <section className="analytics-hardware-card">
          <div>
            <h3>
              <MemoryStick size={16} />
              系统内存
            </h3>
            <Status>{memory.status === 'available' ? '可观测' : '不可用'}</Status>
          </div>
          <strong>
            {bytesLabel(memory.used_bytes)} <span>/ {bytesLabel(memory.total_bytes)}</span>
          </strong>
          <Meter used={memory.used_bytes} total={memory.total_bytes} label="系统实际已用内存" />
          <dl>
            <dt>可用</dt>
            <dd>{bytesLabel(memory.available_bytes)}</dd>
            <dt>Swap 总量 / 空闲</dt>
            <dd>
              {bytesLabel(memory.swap_total_bytes)} / {bytesLabel(memory.swap_free_bytes)}
            </dd>
          </dl>
          <p>{memory.description}</p>
          <small>来源：{memory.source}</small>
        </section>
        <section className="analytics-hardware-card">
          <div>
            <h3>
              <Cpu size={16} />
              CPU 负载
            </h3>
            <Status>{cpu.status === 'available' ? '可观测' : '不可用'}</Status>
          </div>
          <strong>
            {numberLabel(cpu.load_1m, 2)} <span>1 分钟负载</span>
          </strong>
          <dl>
            <dt>5 / 15 分钟负载</dt>
            <dd>
              {numberLabel(cpu.load_5m, 2)} / {numberLabel(cpu.load_15m, 2)}
            </dd>
            <dt>可访问 / 逻辑 CPU</dt>
            <dd>
              {countLabel(cpu.accessible_cpus)} / {countLabel(cpu.logical_cpus)}
            </dd>
            <dt>利用率</dt>
            <dd>不可用</dd>
          </dl>
          <p>{cpu.description}</p>
          <small>来源：{cpu.source}</small>
        </section>
        <section className="analytics-hardware-card">
          <div>
            <h3>
              <HardDrive size={16} />
              存储
            </h3>
            <Status>{storage.status === 'available' ? '可观测' : '不可用'}</Status>
          </div>
          <strong>
            {bytesLabel(storage.used_bytes)} <span>/ {bytesLabel(storage.total_bytes)}</span>
          </strong>
          <Meter used={storage.used_bytes} total={storage.total_bytes} label="实际已用存储空间" />
          <dl>
            <dt>剩余空间</dt>
            <dd>{bytesLabel(storage.free_bytes)}</dd>
            <dt>采集路径</dt>
            <dd>{storage.path}</dd>
          </dl>
          <p>{storage.description}</p>
          <small>来源：{storage.source}</small>
        </section>
      </div>
      <section className="analytics-gpu">
        <div className="analytics-section-heading">
          <h3>
            <Database size={16} />
            GPU
          </h3>
          <Status>{gpu.status === 'available' ? '可观测' : '不可用'}</Status>
        </div>
        {gpu.devices.length ? (
          <div className="analytics-hardware-grid">
            {gpu.devices.map((device) => (
              <section key={device.index} className="analytics-hardware-card">
                <h3>{device.name}</h3>
                <dl>
                  <dt>已用 / 可观测总量</dt>
                  <dd>
                    {bytesLabel(device.memory_used_bytes)} / {bytesLabel(device.memory_total_bytes)}
                  </dd>
                  <dt>利用率</dt>
                  <dd>
                    {device.utilization_percent === null
                      ? '未知'
                      : `${numberLabel(device.utilization_percent)}%`}
                  </dd>
                  <dt>温度</dt>
                  <dd>
                    {device.temperature_celsius === null
                      ? '未知'
                      : `${numberLabel(device.temperature_celsius)} °C`}
                  </dd>
                  <dt>内存类型</dt>
                  <dd>{device.memory_model === 'unified' ? '统一内存' : '未知'}</dd>
                  <dt>与系统内存重叠</dt>
                  <dd>
                    {device.system_memory_overlap === null
                      ? '未知'
                      : device.system_memory_overlap
                        ? '是，不能相加'
                        : '否'}
                  </dd>
                </dl>
              </section>
            ))}
          </div>
        ) : (
          <p className="page-note">没有可显示的实际 GPU 设备读数。</p>
        )}
        <p className="page-note">{gpu.description}</p>
        <p className="page-note">
          来源：{gpu.source}。统一内存与系统内存可能重叠，本页不计算两者相加的总占用。
        </p>
      </section>
      {snapshot.notes.length > 0 && (
        <details className="analytics-source">
          <summary>采集范围与限制</summary>
          <ul>
            {snapshot.notes.map((note, index) => (
              <li key={`${note}:${index}`}>{note}</li>
            ))}
          </ul>
        </details>
      )}
    </AnalyticsSection>
  )
}
