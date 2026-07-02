from agent.discovery.java import _identity_from_cmd
from agent.executor.java_probe import (
    _accept_java_match,
    jar_candidates,
    match_java_line,
    parse_jar_from_cmd,
    parse_ps_java_line,
    score_java_match,
    search_tokens,
)
from agent.models import ServiceConfig, ServiceType


def test_jar_candidates():
    svc = ServiceConfig(id="road_control", host_id="h1", type=ServiceType.JAVA, jar_path="road_control.jar")
    names = jar_candidates(svc)
    assert "road_control.jar" in names


def test_match_java_line():
    line = "28966 java -jar road_control.jar --spring.profiles.active=test"
    assert match_java_line(line, ["road_control.jar"], "road_control")


def test_match_jps_main_class():
    svc = ServiceConfig(
        id="org-apache-hertzbeat-startup-hertzbeatapplication",
        host_id="h1",
        name="Org Apache Hertzbeat Startup Hertzbeatapplication",
        type=ServiceType.JAVA,
        deploy_dir="/data/hertzBeat/apache-hertzbeat-1.8.0-bin",
    )
    line = "424141 HertzBeatApplication"
    assert match_java_line(line, jar_candidates(svc), svc.id, svc)
    assert "hertzbeat" in search_tokens(svc)


def test_no_false_positive_kafka_server_flag():
    svc = ServiceConfig(
        id="server-properties",
        host_id="h1",
        type=ServiceType.JAVA,
        deploy_dir="/data/nq_kafka3.9.1/kafka_2.13-3.9.1",
        name="Server Properties",
    )
    kafka_cmd = (
        "345467 java -Xmx512M -server kafka.Kafka "
        "/data/nq_kafka3.9.1/kafka_2.13-3.9.1/config/server.properties"
    )
    score = score_java_match(svc, kafka_cmd, "/data/nq_kafka3.9.1/kafka_2.13-3.9.1")
    assert not _accept_java_match(svc, kafka_cmd, "/data/nq_kafka3.9.1/kafka_2.13-3.9.1", [9092], score)


def test_no_false_positive_data01_jdk_path():
    svc = ServiceConfig(
        id="kako",
        host_id="h1",
        type=ServiceType.JAVA,
        jar_path="/DATA01/haikang-kako/kako.jar",
        deploy_dir="/DATA01/haikang-kako",
        name="kako",
        listen_ports=[8500],
    )
    hz_cmd = "424141 /DATA01/jdk17/jdk-17.0.1/bin/java -server org.apache.hertzbeat.HertzBeatApplication"
    score = score_java_match(svc, hz_cmd, "/data/hertzBeat/apache-hertzbeat-1.8.0-bin", [1157])
    assert not _accept_java_match(svc, hz_cmd, "/data/hertzBeat/apache-hertzbeat-1.8.0-bin", [1157], score)


def test_road_control_matches_own_jar():
    svc = ServiceConfig(
        id="road_control",
        host_id="h1",
        type=ServiceType.JAVA,
        jar_path="/DATA01/nq_controller/road_control.jar",
        deploy_dir="/DATA01/nq_controller",
        name="road_control",
        listen_ports=[58081],
    )
    cmd = "28966 java -jar /DATA01/nq_controller/road_control.jar"
    score = score_java_match(svc, cmd, "/DATA01/nq_controller", [58081])
    assert _accept_java_match(svc, cmd, "/DATA01/nq_controller", [58081], score)


def test_identity_from_kafka_cmd():
    cmd = (
        "java -Xmx512M -server kafka.Kafka "
        "/data/nq_kafka3.9.1/kafka_2.13-3.9.1/config/server.properties"
    )
    assert _identity_from_cmd(cmd) == ("kafka", "Kafka")


def test_identity_skips_properties_file():
    cmd = "java -jar road_control.jar /data/app/server.properties"
    assert _identity_from_cmd(cmd) == ("road_control", "road_control")


def test_parse_ps_java_line():
    pid, cmd = parse_ps_java_line("28966 java -jar road_control.jar")
    assert pid == 28966
    assert "road_control.jar" in cmd


def test_parse_jar_from_cmd():
    assert parse_jar_from_cmd("java -jar road_control.jar --spring.profiles.active=test") == "road_control.jar"
