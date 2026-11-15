param([string]$Action = "get", [double]$Level = 0.6, [string]$Mute = "False")
Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume { int Reg(); int Unreg(); int GetChannelCount(); int SetMasterVolumeLevel(); int SetMasterVolumeLevelScalar(float f, System.Guid g); int GetMasterVolumeLevel(); int GetMasterVolumeLevelScalar(out float f); int a(); int b(); int c(); int d(); int SetMute(bool m, System.Guid g); int GetMute(out bool m); }
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice { int Activate(ref System.Guid id, int cls, System.IntPtr p, out IAudioEndpointVolume v); }
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator { int _0(); int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice d); }
[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorComObject { }
public class Vol {
  static IAudioEndpointVolume V(){ var e=(IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject()); IMMDevice d; e.GetDefaultAudioEndpoint(0,1,out d); var g=typeof(IAudioEndpointVolume).GUID; IAudioEndpointVolume v; d.Activate(ref g,23,System.IntPtr.Zero,out v); return v; }
  public static string Get(){ float f; bool m; var v=V(); v.GetMasterVolumeLevelScalar(out f); v.GetMute(out m); return f+"|"+m; }
  public static void Set(float f, bool m){ var v=V(); v.SetMasterVolumeLevelScalar(f, System.Guid.Empty); v.SetMute(m, System.Guid.Empty); }
}
'@
if ($Action -eq "get") { [Vol]::Get() } else { [Vol]::Set([float]$Level, [bool]($Mute -eq "True")); "set" }
