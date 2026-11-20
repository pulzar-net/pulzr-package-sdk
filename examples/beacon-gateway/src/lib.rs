//! The Example Beacon Gateway, a made-up gateway that hears Bluetooth beacons
//! and speaks JSON over MQTT, as a gateway support package.
//!
//! It publishes on `example/up/<MAC>`:
//!
//! - `{"gw": "AABBCCDDEEFF", "alive": true, "fw": "1.4.2"}` about once a minute
//! - `{"gw": "AABBCCDDEEFF", "adverts": [{"mac": "BC5729049964", "rssi": -61, "data": "0201061aff..."}]}`
//!
//! takes commands on `example/down/<MAC>` (`{"cmd": "reboot", "seq": 10000042}`)
//! and answers on `example/answer/<MAC>` (`{"seq": 10000042, "ok": true}`).
//!
//! The package translates that into protocol v1: each advert an `up` on the
//! beacon's `ble:` key, the alive message the gateway's `state`, a `reboot`
//! intent a command whose `seq` is the intent's `id`, and the answer a
//! `cmd/res` carrying that id home, so nothing is remembered in between.

use pulzr_package::{
    correlation, export, hexs, ids, json, serde_json, v1, Answer, Event, Package, Value,
};

pub const PREFIX: &str = "exbg";

#[derive(Default)]
pub struct BeaconGateway;

impl Package for BeaconGateway {
    fn handle(&mut self, event: Event) -> Answer {
        let mut answer = Answer::default();
        match event {
            Event::Message {
                topic,
                payload,
                received,
            } => {
                let Ok(message) = serde_json::from_slice::<Value>(&payload) else {
                    answer.log_warning(format!("unreadable message on {topic}"));
                    return answer;
                };
                if topic.starts_with("example/up/") {
                    from_gateway(&message, received, &mut answer);
                } else if topic.starts_with("example/answer/") {
                    from_answer(&message, &mut answer);
                }
            }
            Event::Intent {
                adapter,
                key,
                id,
                name,
                expires,
                now,
                ..
            } => {
                intent(&adapter, &key, &id, &name, expires, now, &mut answer);
            }
            _ => {}
        }
        answer
    }
}

fn from_gateway(m: &Value, received: f64, answer: &mut Answer) {
    let Some(gw) = m
        .get("gw")
        .and_then(Value::as_str)
        .and_then(ids::eui_for_mac)
    else {
        return;
    };
    let adapter = ids::adapter(PREFIX, &gw);
    if m.get("alive").and_then(Value::as_bool) == Some(true) {
        let mut state = json!({"v": 1, "online": true, "last_heard": received});
        if let Some(fw) = m.get("fw").and_then(Value::as_str) {
            state["fw"] = fw.into();
        }
        answer.protocol(&adapter, ids::key("gw", &gw), "state", state);
    }
    for advert in m
        .get("adverts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let (Some(beacon), Some(bytes)) = (
            advert
                .get("mac")
                .and_then(Value::as_str)
                .and_then(ids::eui_for_mac),
            advert
                .get("data")
                .and_then(Value::as_str)
                .and_then(hexs::decode),
        ) else {
            continue;
        };
        let rx = v1::Rx {
            via: ids::key("gw", &gw),
            ts: received,
            time: "received",
            rssi: advert.get("rssi").and_then(Value::as_i64),
        };
        let key = ids::key("ble", &beacon);
        let up = v1::Up::raw(&key, &bytes, received, rx);
        answer.protocol(
            &adapter,
            key,
            "up",
            serde_json::to_value(&up).unwrap_or(Value::Null),
        );
    }
}

fn from_answer(m: &Value, answer: &mut Answer) {
    let (Some(mac), Some(seq)) = (
        m.get("gw").and_then(Value::as_str),
        m.get("seq").and_then(Value::as_u64),
    ) else {
        return;
    };
    // A small seq is another sender's command, not ours to answer.
    let (Some(gw), Some(id)) = (ids::eui_for_mac(mac), correlation::id_for_seq(seq)) else {
        return;
    };
    let result = if m.get("ok").and_then(Value::as_bool) == Some(true) {
        v1::CmdResult::ok(&id)
    } else {
        v1::CmdResult::failed(
            &id,
            "error",
            m.get("error")
                .and_then(Value::as_str)
                .unwrap_or("The gateway refused it"),
        )
    };
    answer.result(&ids::adapter(PREFIX, &gw), &ids::key("gw", &gw), result);
}

fn intent(
    adapter: &str,
    key: &str,
    id: &str,
    name: &str,
    expires: Option<f64>,
    now: f64,
    answer: &mut Answer,
) {
    if expires.is_some_and(|e| e < now) {
        answer.result(
            adapter,
            key,
            v1::CmdResult::failed(id, "expired", "It expired before it was sent"),
        );
        return;
    }
    let Some(gw) = key.strip_prefix("gw:").and_then(ids::mac_for_eui) else {
        answer.result(
            adapter,
            key,
            v1::CmdResult::refused(id, "unknown", format!("No {name} for {key}")),
        );
        return;
    };
    let Some(seq) = correlation::seq_for_id(id) else {
        answer.result(
            adapter,
            key,
            v1::CmdResult::refused(
                id,
                "invalid",
                "The id must be decimal: it is the gateway's seq",
            ),
        );
        return;
    };
    match name {
        "reboot" => {
            answer.publish_json(
                format!("example/down/{gw}"),
                &json!({"cmd": "reboot", "seq": seq}),
            );
            answer.result(adapter, key, v1::CmdResult::sent(id));
        }
        _ => {
            answer.result(
                adapter,
                key,
                v1::CmdResult::refused(id, "unknown", format!("The gateway does not know {name}")),
            );
        }
    }
}

export!(BeaconGateway);
