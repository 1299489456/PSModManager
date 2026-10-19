// Original PSModManager synthetic metadata only; not MelonLoader code.
using System;
namespace MelonLoader {
    [AttributeUsage(AttributeTargets.Assembly)]
    public sealed class MelonInfoAttribute : Attribute {
        public MelonInfoAttribute(Type type, string name, string version, string author) {}
    }
    [AttributeUsage(AttributeTargets.Assembly)]
    public sealed class MelonGameAttribute : Attribute {
        public MelonGameAttribute(string developer, string name) {}
    }
    public abstract class MelonMod {}
}
