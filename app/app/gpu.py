import psutil

MODES = ('POSTER_MAX', 'BALANCED', 'ADOBE_PRIORITY', 'PAUSE_AI')


def snapshot(mode):
    result = {'mode':mode,'available':False,'name':None,'utilization':None,'free_mb':None,
              'total_mb':None,'encoder':None,'decoder':None,'adobe':[],
              'inference_backend':'由应用当前计算后端决定','per_process_supported':False}
    for p in psutil.process_iter(['name']):
        try:
            name = p.info['name'] or ''
            if any(x in name.lower() for x in ('adobe premiere', 'adobe media encoder')):
                result['adobe'].append(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    initialized = False
    try:
        import pynvml as nv
        nv.nvmlInit()
        initialized = True
        handle = nv.nvmlDeviceGetHandleByIndex(0)
        result['available'] = True
        result['name'] = nv.nvmlDeviceGetName(handle)
        for key, getter in [('utilization', lambda: nv.nvmlDeviceGetUtilizationRates(handle).gpu),
                            ('encoder', lambda: nv.nvmlDeviceGetEncoderUtilization(handle)[0]),
                            ('decoder', lambda: nv.nvmlDeviceGetDecoderUtilization(handle)[0])]:
            try:
                result[key] = getter()
            except nv.NVMLError:
                pass
        try:
            mem = nv.nvmlDeviceGetMemoryInfo(handle)
            result.update(free_mb=mem.free//1048576, total_mb=mem.total//1048576)
        except nv.NVMLError:
            pass
    except Exception:
        pass
    finally:
        if initialized:
            nv.nvmlShutdown()
    return result
