# Algebraic Control Architecture Based on Monoids: A Domain Typology Comparison Between IEC 61131-3 (ST) and Rust

This paper explores how to fulfill the theoretical requirement of "isolating pure business logic from side effects" by introducing the concept of a Monoid into industrial control system architecture. The discussion focuses on the mapping and implementation of abstract data types, providing a rigorous, metaphysical, one-to-one comparison between traditional PLC Structured Text (ST) and modern system-level languages (using Rust as the exemplar) strictly through the lens of type systems.

## 1. Theoretical Foundation: Monoid Mappings in Cybernetics

In the MonoPLC architecture (or more broadly, single-threaded event-driven abstract control architectures), the core logic is a deterministic discrete state transition mapping: $f: S_t \times E_{in} \to S_{t+1} \times E_{out}$. 

To achieve architectural orthogonality and data decoupling, the output set $E_{out}$ (the set of Side-Effects) must algebraically form a **Monoid** $(M, \oplus, e)$. This dictates that the underlying implementation language must not only be capable of declaring discrete event entities $a \in M$, but must also provide safe data structures and operators that guarantee **Type Closure** and natively support **Associativity** ($a \oplus (b \oplus c)$).

The following sections deconstruct and contrast the realization of this operator and structure within the two language paradigms.

---

## 2. Core Algebraic Mapping Comparison: Data Entities and Heterogeneous Sets

To physically isolate various external side effects (e.g., high-frequency actions of different dimensions such as physical valve actuation or network packet transmission), we must encapsulate and land them within a single data pipeline. This means the algebraic system requires the underlying "container" to possess both the capacity to bear the business payload and **Type Equivalence**, ensuring that the operator acts upon objects belonging to the same set.

### 2.1 [Implementation Contrast A] The Carrier Definition of Monoid Entities

Traditional ST lacks the ability to describe Disjoint Unions; conversely, in Rust, "Sum Types" (or Enumerations) natively represent disjoint algebraic sets.

#### Wide Struct Homogenization in IEC 61131-3 (ST)

```iecst
TYPE DUT_Effect : STRUCT
    EType   : INT; // Identifier: e.g., 1=Valve, 2=Iot
    Target  : STRING[32]; // Target name
    Value   : LREAL;      // Used for valves
    Payload : STRING[255];// Used for network payloads
END_STRUCT
END_TYPE
```

**Theoretical Analysis:**
To achieve type homogeneity (in order to fit instances into an array), this structure forcibly merges variables from mutually exclusive business domains. This results in a highly redundant, loosely-cohesive data object. For instance, mutating a boolean valve state inevitably carries up to 255 bytes of a useless `Payload` burden.
Furthermore, due to the lack of safe abstraction, runtime functions are highly susceptible to violating state constraints through misaligned data access (e.g., illegally accessing an uninitialized `Payload` field when the identifier is clearly `EFF_VALVE_CTRL`).

#### Sum Type Segmentation in Rust

```rust
pub enum Effect {
    None, // Candidate for the identity element 'e'
    ValveCtrl { target: String, state: bool },
    IotPublish { topic: String, payload: String },
}
```

**Theoretical Analysis:**
By leveraging Algebraic Data Types (ADT), this achieves perfect **Physical Type Segregation**.
Under the hood, memory alignment uses a `Tagged Union`, completely avoiding any overflow overhead from meaningless data. Critically, in the downstream decoupled architecture, the mandatory use of Pattern Matching physically blocks cross-domain unauthorized access at compile time, guaranteeing absolute deterministic running states for internal logic.

---

## 3. Core Algebraic Mapping Comparison: Physical Unfolding of the Binary Associative Operator

The vitality of the Monoid structure lies in the binary associative operator $\oplus$ (often used by systems to merge high-frequency or parallel event sets). However, in real-time control scenarios, due to fundamental differences in memory allocation patterns, the implementation cost and engineering risk of this operator differ drastically between the two type systems.

### 3.1 [Implementation Contrast B] Aggregation Evolution of Monoid Objects as $n \to \infty$

#### Forward Copy Degradation Based on Static Memory Pools in IEC 61131-3 (ST)

```iecst
TYPE DUT_Effect_Monoid : STRUCT
    Count   : UINT; 
    Effects : ARRAY[1..100] OF DUT_Effect;
END_STRUCT
END_TYPE

FUNCTION FC_Combine : DUT_Effect_Monoid
// Requires a cumbersome FOR loop here to deep-copy the two arrays...
```

**Theoretical Analysis:**
Because ST does not support safe, contiguous Dynamic Memory Allocation, its set boundary is forced to degrade into compile-time static constant array limits (e.g., `MAX = 100`).
When environmental injections enter an "unpredictable, unsteady state" generating high-frequency storms, the constant limit is rapidly breached. This leads to severe Out-of-Bound Memory Violations or implicit packet drops, fundamentally destroying the Monoid's requirement for **Closure**. Moreover, massive Deep Copies triggered by every `FC_Combine` execution introduce severe real-time scheduling "Jitter" within the microsecond-level microkernel.

#### Dynamic Protocol Derivation and Native Intent Redefinition in Rust

```rust
use std::ops::Add;

#[derive(Default)]
pub struct EffectMonoid(pub Vec<Effect>);

impl Add for EffectMonoid {
    type Output = Self;
    fn add(mut self, mut other: Self) -> Self {
        self.0.append(&mut other.0);
        self
    }
}
```

**Theoretical Analysis:**
Building upon the underlying Allocator of modern systems, the relinking of dynamic arrays (`Vec`) in Rust grants the operator a smooth set expansion with an optimal data shifting performance upper bound of $O(1)$ or strictly $O(n)$.
Most importantly, based on Traits design, we natively bind the associative operator $\oplus$ to language-level constructs (like `+` or iterator `fold/reduce`). This signifies that the architecture no longer obsesses over "how to implement combination"; rather, combination is abstracted into a secure, domain-language-driven protocol protected by compile-time Zero-Cost Abstractions.

---

## 4. Conclusion

In the process of constructing single-threaded event-driven architectures and striving towards formalization by "isolating side effects", mathematical mapping (specifically, the strict enforcement of Monoid behavior) places stringent demands on the language ecology of the underlying carrier.

Traditional IEC 61131-3 (ST) languages, lacking the concept of Sum Types for structures and the management support for dynamic memory mapping boundaries, expose their "implementation projections" of abstract models to implicit risks. Developers are forced to manually guard against system crashes using "constant-capped arrays" and "broadly compromised super-set data types", breaking the original intent of formal expression.

Conversely, modern high-level categorical entities featuring multidimensional type systems (like Rust) not only grant the operators of Monoids pristine formal purity via Homomorphism but also leverage strict model constraints at compile time to eliminate the hidden dangers of data contamination when crossing Domain boundaries. This establishes a controllable mathematical foundation for the structural advancement of automation logic toward greater theoretical rigor.
