//! Settings in a vendor's own vocabulary: sections of values, as most
//! gateways group them. The platform computes nothing about a vendor's
//! settings; a package's `settings.set` does, with these.

use serde_json::{Map, Value};

/// `base` with `over` laid on top, section by section: a section in both is
/// merged key by key, anything else replaced.
pub fn merge(base: &Map<String, Value>, over: &Map<String, Value>) -> Map<String, Value> {
    let mut out = base.clone();
    for (section, values) in over {
        match (out.get_mut(section), values) {
            (Some(Value::Object(have)), Value::Object(add)) => {
                for (k, v) in add {
                    have.insert(k.clone(), v.clone());
                }
            }
            _ => {
                out.insert(section.clone(), values.clone());
            }
        }
    }
    out
}

fn same(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => x.as_f64() == y.as_f64(),
        _ => a == b,
    }
}

/// What of `want` the gateway does not have, as a body to send; empty when in
/// sync. Only the settings `want` names are compared, a number equal to
/// another of another type (60 and 60.0) is no difference, and a setting the
/// gateway does not report at all goes along with a real change but never
/// makes one on its own (some firmware takes a setting and never echoes it).
pub fn drift(want: &Map<String, Value>, reported: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    let mut unreported = Map::new();
    for (section, values) in want {
        let Value::Object(values) = values else {
            match reported.get(section) {
                None => {
                    unreported.insert(section.clone(), values.clone());
                }
                Some(have) if !same(values, have) => {
                    out.insert(section.clone(), values.clone());
                }
                _ => {}
            }
            continue;
        };
        let empty = Map::new();
        let have = reported
            .get(section)
            .and_then(Value::as_object)
            .unwrap_or(&empty);
        for (k, v) in values {
            let target = match have.get(k) {
                None => &mut unreported,
                Some(h) if !same(v, h) => &mut out,
                _ => continue,
            };
            target
                .entry(section.clone())
                .or_insert_with(|| Value::Object(Map::new()))
                .as_object_mut()
                .map(|m| m.insert(k.clone(), v.clone()));
        }
    }
    if !out.is_empty() {
        for (section, values) in unreported {
            match (out.get_mut(&section), values) {
                (Some(Value::Object(o)), Value::Object(add)) => o.extend(add),
                (_, v) => {
                    out.insert(section, v);
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn obj(v: Value) -> Map<String, Value> {
        v.as_object().unwrap().clone()
    }

    #[test]
    fn merging_and_drift() {
        let want = merge(
            &obj(json!({"ble": {"rssi": -90, "scan": 1}, "common": {"upload": 10}})),
            &obj(json!({"ble": {"rssi": -80}})),
        );
        assert_eq!(
            want,
            obj(json!({"ble": {"rssi": -80, "scan": 1}, "common": {"upload": 10}}))
        );
        let reported =
            obj(json!({"ble": {"rssi": -90, "scan": 1, "other": 0}, "common": {"upload": 10.0}}));
        assert_eq!(drift(&want, &reported), obj(json!({"ble": {"rssi": -80}})));
        // Never reported: alone not drift, beside a change sent along.
        let mut want2 = want.clone();
        want2["common"]
            .as_object_mut()
            .unwrap()
            .insert("alive".into(), json!(60));
        assert_eq!(
            drift(&want2, &reported),
            obj(json!({"ble": {"rssi": -80}, "common": {"alive": 60}}))
        );
        let in_sync = obj(json!({"ble": {"rssi": -80, "scan": 1}, "common": {"upload": 10}}));
        assert!(drift(&want2, &in_sync).is_empty());
    }
}
