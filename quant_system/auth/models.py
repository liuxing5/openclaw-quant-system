"""
用户权限系统数据模型
基于RBAC的多租户权限管理
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Table, Text
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from passlib.context import CryptContext

Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 关联表：用户-角色多对多关系
user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True)
)

# 关联表：角色-权限多对多关系
role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id'), primary_key=True)
)

# 关联表：用户-团队多对多关系
user_teams = Table(
    'user_teams',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('team_id', Integer, ForeignKey('teams.id'), primary_key=True)
)


class User(Base):
    """用户模型"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(120), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)
    full_name = Column(String(128))
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)
    
    # 关系
    roles = relationship('Role', secondary=user_roles, back_populates='users')
    teams = relationship('Team', secondary=user_teams, back_populates='members')
    
    def set_password(self, password):
        """设置密码哈希"""
        self.password_hash = pwd_context.hash(password)
    
    def verify_password(self, password):
        """验证密码"""
        return pwd_context.verify(password, self.password_hash)
    
    def has_permission(self, permission_name):
        """检查用户是否有特定权限"""
        if self.is_superuser:
            return True
        
        for role in self.roles:
            for permission in role.permissions:
                if permission.name == permission_name:
                    return True
        return False
    
    def has_role(self, role_name):
        """检查用户是否有特定角色"""
        for role in self.roles:
            if role.name == role_name:
                return True
        return False


class Role(Base):
    """角色模型"""
    __tablename__ = 'roles'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(Text)
    is_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    users = relationship('User', secondary=user_roles, back_populates='roles')
    permissions = relationship('Permission', secondary=role_permissions, back_populates='roles')


class Permission(Base):
    """权限模型"""
    __tablename__ = 'permissions'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False, index=True)
    description = Column(Text)
    resource_type = Column(String(64))  # 资源类型: strategy, backtest, data, system
    action = Column(String(32))  # 操作: create, read, update, delete, execute
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    roles = relationship('Role', secondary=role_permissions, back_populates='permissions')


class Team(Base):
    """团队模型（多租户支持）"""
    __tablename__ = 'teams'
    
    id = Column(Integer, primary_key=True)
    name = Column(String(128), unique=True, nullable=False)
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    members = relationship('User', secondary=user_teams, back_populates='teams')
    strategies = relationship('TeamStrategy', back_populates='team')
    backtests = relationship('TeamBacktest', back_populates='team')


class TeamStrategy(Base):
    """团队策略关联"""
    __tablename__ = 'team_strategies'
    
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    strategy_id = Column(String(64), nullable=False)  # 策略标识符
    access_level = Column(String(32), default='read')  # read, write, admin
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    team = relationship('Team', back_populates='strategies')


class TeamBacktest(Base):
    """团队回测结果关联"""
    __tablename__ = 'team_backtests'
    
    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey('teams.id'), nullable=False)
    backtest_id = Column(String(64), nullable=False)  # 回测结果标识符
    access_level = Column(String(32), default='read')  # read, write, admin
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # 关系
    team = relationship('Team', back_populates='backtests')


class AuditLog(Base):
    """审计日志"""
    __tablename__ = 'audit_logs'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=True)
    username = Column(String(64))
    action = Column(String(64), nullable=False)  # 操作类型
    resource_type = Column(String(64))  # 资源类型
    resource_id = Column(String(128))  # 资源标识符
    details = Column(Text)  # 操作详情
    ip_address = Column(String(45))  # IPv4或IPv6地址
    user_agent = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)


class APIToken(Base):
    """API访问令牌"""
    __tablename__ = 'api_tokens'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    name = Column(String(128), nullable=False)
    token = Column(String(64), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    last_used = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    permissions = Column(Text)  # JSON格式的权限列表
    
    # 关系
    user = relationship('User')


# 预定义的权限和角色
PREDEFINED_PERMISSIONS = [
    # 系统权限
    ('system.admin', '系统管理', 'system', 'admin'),
    ('system.config', '系统配置', 'system', 'update'),
    
    # 用户管理权限
    ('user.read', '查看用户', 'user', 'read'),
    ('user.create', '创建用户', 'user', 'create'),
    ('user.update', '更新用户', 'user', 'update'),
    ('user.delete', '删除用户', 'user', 'delete'),
    
    # 团队管理权限
    ('team.read', '查看团队', 'team', 'read'),
    ('team.create', '创建团队', 'team', 'create'),
    ('team.update', '更新团队', 'team', 'update'),
    ('team.delete', '删除团队', 'team', 'delete'),
    
    # 策略权限
    ('strategy.read', '查看策略', 'strategy', 'read'),
    ('strategy.create', '创建策略', 'strategy', 'create'),
    ('strategy.update', '更新策略', 'strategy', 'update'),
    ('strategy.delete', '删除策略', 'strategy', 'delete'),
    ('strategy.execute', '执行策略', 'strategy', 'execute'),
    
    # 回测权限
    ('backtest.read', '查看回测', 'backtest', 'read'),
    ('backtest.create', '创建回测', 'backtest', 'create'),
    ('backtest.update', '更新回测', 'backtest', 'update'),
    ('backtest.delete', '删除回测', 'backtest', 'delete'),
    ('backtest.execute', '执行回测', 'backtest', 'execute'),
    
    # 数据权限
    ('data.read', '查看数据', 'data', 'read'),
    ('data.download', '下载数据', 'data', 'execute'),
    ('data.update', '更新数据', 'data', 'update'),
    
    # 监控权限
    ('dashboard.read', '查看监控面板', 'dashboard', 'read'),
    ('report.read', '查看报告', 'report', 'read'),
    ('report.generate', '生成报告', 'report', 'execute'),
]

PREDEFINED_ROLES = [
    # 管理员：所有权限
    ('admin', '系统管理员', [
        'system.admin', 'system.config',
        'user.read', 'user.create', 'user.update', 'user.delete',
        'team.read', 'team.create', 'team.update', 'team.delete',
        'strategy.read', 'strategy.create', 'strategy.update', 'strategy.delete', 'strategy.execute',
        'backtest.read', 'backtest.create', 'backtest.update', 'backtest.delete', 'backtest.execute',
        'data.read', 'data.download', 'data.update',
        'dashboard.read', 'report.read', 'report.generate'
    ]),
    
    # 研究员：策略开发和回测
    ('researcher', '量化研究员', [
        'strategy.read', 'strategy.create', 'strategy.update', 'strategy.delete', 'strategy.execute',
        'backtest.read', 'backtest.create', 'backtest.update', 'backtest.delete', 'backtest.execute',
        'data.read', 'data.download',
        'dashboard.read', 'report.read', 'report.generate'
    ]),
    
    # 观察员：只读权限
    ('observer', '观察员', [
        'strategy.read',
        'backtest.read',
        'data.read',
        'dashboard.read', 'report.read'
    ]),
    
    # 访客：有限权限
    ('guest', '访客', [
        'dashboard.read'
    ]),
]