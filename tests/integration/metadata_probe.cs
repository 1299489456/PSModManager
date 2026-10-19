// Owned negative probe. This is trusted test code, never a parsed DLL.
using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text.Json;
using System.Threading;

class Probe {
    [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] struct Startup {
        public int cb; public string reserved, desktop, title; public int x,y,xsize,ysize,xc,yc,fill,flags;
        public short show, reservedSize; public IntPtr reserved2, input, output, error;
    }
    [StructLayout(LayoutKind.Sequential)] struct ProcessInfo { public IntPtr process, thread; public int pid, tid; }
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool CreateProcess(string app, string cmd, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref Startup startup, out ProcessInfo info);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr h, uint code);
    [DllImport("kernel32.dll")] static extern uint SetErrorMode(uint mode);
    [DllImport("kernel32.dll")] static extern uint GetErrorMode();
    static object Attempt(Action action) {
        Console.Error.WriteLine("attempt:" + action.Method.Name);
        try { action(); return new { denied=false, error="" }; }
        catch (Exception e) { return new { denied=true, error=e.GetType().Name, code=e is SocketException socket ? socket.NativeErrorCode : e.HResult }; }
    }
    static object Child(uint flags) {
        Console.Error.WriteLine("child:" + flags);
        var startup=new Startup { cb=Marshal.SizeOf<Startup>() };
        bool ok=CreateProcess(Environment.ProcessPath, "\""+Environment.ProcessPath+"\" --info", IntPtr.Zero,IntPtr.Zero,false,flags|0x08000000,IntPtr.Zero,null,ref startup,out var pi);
        int error=Marshal.GetLastWin32Error();
        if(ok) { TerminateProcess(pi.process, 77); CloseHandle(pi.thread); CloseHandle(pi.process); }
        return new { denied=!ok, error=error };
    }
    static int Main() {
        Console.Error.WriteLine("owned-probe-entered");
        var request=JsonDocument.Parse(Console.In.ReadToEnd()).RootElement;
        string mode=request.GetProperty("mode").GetString();
        Console.Error.WriteLine("owned-probe-request:" + mode);
        if(mode=="error_mode") { Console.WriteLine(JsonSerializer.Serialize(new { error_mode=GetErrorMode() })); return 0; }
        if(mode=="hang") { Thread.Sleep(Timeout.Infinite); return 0; }
        // Hard termination emulates a helper disappearing without managed cleanup.
        if(mode=="crash") { TerminateProcess(new IntPtr(-1),0xC0000005); return 99; }
        if(mode=="failfast") { SetErrorMode(3); Environment.FailFast("owned probe crash"); return 99; }
        if(mode=="flood") { while(true) Console.Write(new string('x',8192)); }
        if(mode=="memory") {
            var keep=new List<byte[]>(); long allocated=0;
            try { while(true) { var block=new byte[8*1024*1024]; for(int n=0;n<block.Length;n+=4096) block[n]=1; keep.Add(block); allocated+=block.Length; } }
            catch(OutOfMemoryException) { Console.WriteLine(JsonSerializer.Serialize(new { memory_denied=true, allocated=allocated })); return 23; }
        }
        string outside=request.GetProperty("outside").GetString();
        string input=request.GetProperty("input").GetString();
        int port=request.GetProperty("port").GetInt32();
        if(mode=="network_control") {
            var network=new Dictionary<string,object> {
                ["bind_loopback"]=Attempt(()=> { using var s=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp); s.Bind(new IPEndPoint(IPAddress.Loopback,0)); s.Listen(1); }),
                ["connect_loopback"]=Attempt(()=> { using var s=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp); using var cancel=new CancellationTokenSource(1000); s.ConnectAsync(new IPEndPoint(IPAddress.Loopback,port),cancel.Token).GetAwaiter().GetResult(); })
            };
            Console.WriteLine(JsonSerializer.Serialize(network)); return 0;
        }
        var results=new Dictionary<string,object> {
            ["read_outside"]=Attempt(()=>File.ReadAllText(outside)),
            ["write_outside"]=Attempt(()=>File.AppendAllText(outside,"changed")),
            ["write_input"]=Attempt(()=>File.AppendAllText(input,"changed")),
            ["bind_loopback"]=Attempt(()=> { using var s=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp); s.Bind(new IPEndPoint(IPAddress.Loopback,0)); s.Listen(1); }),
            ["connect_loopback"]=Attempt(()=> { using var s=new Socket(AddressFamily.InterNetwork,SocketType.Stream,ProtocolType.Tcp); using var cancel=new CancellationTokenSource(1000); s.ConnectAsync(new IPEndPoint(IPAddress.Loopback,port),cancel.Token).GetAwaiter().GetResult(); }),
            ["child"]=Child(0), ["breakaway_child"]=Child(0x01000000),
            ["input_read"]=File.ReadAllText(input)
        };
        Console.WriteLine(JsonSerializer.Serialize(results)); return 0;
    }
}
