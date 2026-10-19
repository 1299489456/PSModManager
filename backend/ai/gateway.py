"""Bounded OpenAI-compatible text/function SSE, without legacy imports."""
import asyncio
from dataclasses import dataclass
import json
import re
from urllib.parse import urlsplit, urlunsplit
from .budgets import AIError, RequestBudget


@dataclass(frozen=True)
class StreamReply:
    text: str
    tool_calls: tuple


def normalize_url(value):
    if not isinstance(value, str) or len(value)>2048 or any(c.isspace() for c in value) or any(c in value for c in '\\?#'):
        raise AIError('AI_SETTINGS_INVALID')
    try:
        uri = urlsplit(value)
        if not uri.hostname or uri.username or uri.password or uri.scheme not in {'http','https'}:
            raise ValueError()
        if uri.scheme == 'http' and uri.hostname not in {'127.0.0.1','localhost','::1'}:
            raise ValueError()
        if uri.port is not None and not 1<=uri.port<=65535:
            raise ValueError()
        path = uri.path.rstrip('/')
        if path.endswith('/chat/completions'):
            raise ValueError()
        if not path:
            path = '/v1'
        return urlunsplit((uri.scheme,uri.netloc,path,'',''))
    except ValueError:
        raise AIError('AI_SETTINGS_INVALID') from None


def local_endpoint(config):
    return urlsplit(config['base_url']).hostname in {'127.0.0.1','localhost','::1'}


class Gateway:
    def stream(self, config, key, messages, check, emit, *, tools=None, deadline=None, budget=None):
        budget = budget or RequestBudget(deadline=deadline)
        budget.check(check)
        budget.provider(messages)
        return asyncio.run(self._stream(config, key, messages, check, emit, tools, budget))

    async def _stream(self, config, key, messages, check, emit, tools, budget):
        import httpx
        async def consume():
            headers = {'Accept':'text/event-stream'}
            if key:
                headers['Authorization'] = 'Bearer '+key
            body = dict(model=config['model'],messages=messages,stream=True)
            if tools:
                body['tools'] = tools
                body['tool_choice'] = 'auto'
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(15,connect=5,write=5,pool=5)) as client:
                async with client.stream('POST',config['base_url']+'/chat/completions',headers=headers,
                        json=body) as response:
                    status = response.status_code
                    if status != 200:
                        raise AIError({401:'AI_AUTH_FAILED',403:'AI_AUTH_FAILED',429:'AI_RATE_LIMIT'}.get(status,
                            'AI_MODEL_UNAVAILABLE' if status==404 else 'AI_REDIRECT' if 300<=status<400 else 'AI_PROVIDER_FAILED'))
                    if 'text/event-stream' not in response.headers.get('content-type','').lower():
                        raise AIError('AI_PROTOCOL_ERROR')
                    buffer, event, output, finished = b'', [], '', False
                    calls, finish_reason = {}, None
                    def result():
                        if not finished or not output and not calls:
                            raise AIError('AI_STREAM_INCOMPLETE')
                        if calls:
                            if finish_reason != 'tool_calls' or sorted(calls) != list(range(len(calls))):
                                raise AIError('AI_PROTOCOL_ERROR')
                            ids = set()
                            for call in calls.values():
                                if (not re.fullmatch(r'[a-zA-Z0-9_-]{1,128}', call['id'])
                                        or call['id'] in ids or not call['function']['name'] or call['type'] != 'function'):
                                    raise AIError('AI_PROTOCOL_ERROR')
                                ids.add(call['id'])
                        return output if tools is None else StreamReply(output, tuple(calls[i] for i in sorted(calls)))
                    async for chunk in response.aiter_bytes():
                        budget.wire(len(chunk))
                        buffer += chunk
                        while b'\n' in buffer:
                            line, buffer = buffer.split(b'\n',1)
                            if len(line)>262144:
                                raise AIError('AI_STREAM_LIMIT')
                            line = line.rstrip(b'\r')
                            if line.startswith(b'data:'):
                                event.append(line[5:].lstrip())
                                if sum(map(len,event))>262144:
                                    raise AIError('AI_STREAM_LIMIT')
                            elif not line and event:
                                data = b'\n'.join(event); event=[]
                                if data == b'[DONE]':
                                    return result()
                                try:
                                    budget.check(check)
                                    budget.frame()
                                    item = json.loads(data)
                                    if item.get('error'):
                                        raise AIError('AI_PROVIDER_FAILED')
                                    choices = item['choices']
                                    if not choices:
                                        continue  # Usage-only chunk.
                                    if len(choices) != 1 or choices[0].get('index',0) != 0 or finished:
                                        raise ValueError()
                                    choice = choices[0]
                                    delta = choice.get('delta',{})
                                    if delta.get('function_call') or delta.get('tool_calls') and tools is None:
                                        raise AIError('AI_UNSUPPORTED_RESPONSE')
                                    fragments = delta.get('tool_calls') or []
                                    if not isinstance(fragments,list):
                                        raise ValueError()
                                    for fragment in fragments:
                                        index = fragment['index']
                                        # Parse a bounded provider batch separately
                                        # from the smaller local execution budget.
                                        if type(index) is not int or not 0 <= index < 16:
                                            raise AIError('AI_TOOL_RESPONSE_LIMIT')
                                        call = calls.setdefault(index,dict(id='',type='',function=dict(name='',arguments='')))
                                        for field in ('id','type'):
                                            value = fragment.get(field)
                                            if value is not None:
                                                if not isinstance(value,str) or call[field] and call[field] != value:
                                                    raise ValueError()
                                                call[field] = value
                                        function = fragment.get('function') or {}
                                        for field, limit in (('name',128),('arguments',4096)):
                                            value = function.get(field)
                                            if value is not None:
                                                if not isinstance(value,str):
                                                    raise ValueError()
                                                call['function'][field] += value
                                            if len(call['function'][field].encode('utf-8')) > limit:
                                                raise AIError('AI_TOOL_ARGUMENTS')
                                    text = delta.get('content') or ''
                                    if not isinstance(text,str):
                                        raise ValueError()
                                    remaining = max(0,budget.limits.output_bytes-budget.output_bytes)
                                    if len(text.encode('utf-8'))>remaining:
                                        accepted=text.encode('utf-8')[:remaining].decode('utf-8',errors='ignore')
                                        if accepted:
                                            output += accepted
                                            emit(output,False)
                                    budget.text(text)
                                    output += text
                                    if text:
                                        emit(output,False)
                                    reason = choice.get('finish_reason')
                                    if reason is not None:
                                        if reason != 'stop' and not (tools is not None and reason == 'tool_calls'):
                                            raise AIError('AI_PROVIDER_TRUNCATED' if reason=='length' else 'AI_UNSUPPORTED_RESPONSE')
                                        if reason == 'stop' and calls or reason == 'tool_calls' and not calls:
                                            raise ValueError()
                                        finish_reason = reason
                                        finished = True
                                except (ValueError,KeyError,TypeError,UnicodeError,AttributeError,RecursionError) as exc:
                                    if isinstance(exc,AIError):
                                        raise
                                    raise AIError('AI_PROTOCOL_ERROR') from None
                        if len(buffer)>262144:
                            raise AIError('AI_STREAM_LIMIT')
                    if buffer.strip() or event:
                        raise AIError('AI_STREAM_INCOMPLETE')
                    return result()
        task = asyncio.create_task(consume())
        try:
            while not task.done():
                budget.check(check)
                await asyncio.wait({task},timeout=.05)
            budget.check(check)
            return await task
        except httpx.TimeoutException:
            raise AIError('AI_TIMEOUT') from None
        except httpx.HTTPError:
            raise AIError('AI_CONNECTION_FAILED') from None
        finally:
            if not task.done():
                task.cancel()
            # Retrieve the result even if cancellation/deadline raced completion.
            await asyncio.gather(task, return_exceptions=True)
